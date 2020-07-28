import base64
from jumpscale.loader import j
from jumpscale.sals.chatflows.chatflows import StopChatFlow
from jumpscale.clients.explorer.models import NextAction, Type, DiskType, Mode
from nacl.public import Box
import netaddr
import time
from collections import defaultdict
from decimal import Decimal
import gevent
from jumpscale.sals.zos.network import wg_routing_ip


class NetworkView:
    def __init__(self, name, workloads=None):
        self.name = name
        if not workloads:
            workloads = j.sals.zos.workloads.list(j.core.identity.me.tid, NextAction.DEPLOY)
        self.workloads = workloads
        self.used_ips = []
        self.network_workloads = []
        self._fill_used_ips(self.workloads)
        self._init_network_workloads(self.workloads)
        self.iprange = self.network_workloads[0].network_iprange

    def _init_network_workloads(self, workloads):
        for workload in workloads:
            if workload.info.next_action != NextAction.DEPLOY:
                continue
            if workload.info.workload_type == Type.Network_resource and workload.name == self.name:
                self.network_workloads.append(workload)

    def _fill_used_ips(self, workloads):
        for workload in workloads:
            if workload.info.next_action != NextAction.DEPLOY:
                continue
            if workload.info.workload_type == Type.Kubernetes:
                self.used_ips.append(workload.ipaddress)
            elif workload.info.workload_type == Type.Container:
                for conn in workload.network_connection:
                    if conn.network_id == self.name:
                        self.used_ips.append(conn.ipaddress)

    def add_node(self, node, pool_id):
        used_ip_ranges = set()
        for workload in self.network_workloads:
            if workload.info.node_id == node.node_id:
                return
            used_ip_ranges.add(workload.iprange)
            for peer in workload.peers:
                used_ip_ranges.add(peer.iprange)
        else:
            network_range = netaddr.IPNetwork(self.iprange)
            for idx, subnet in enumerate(network_range.subnet(24)):
                if str(subnet) not in used_ip_ranges:
                    break
            else:
                raise StopChatFlow("Failed to find free network")
            network = j.sals.zos.network.create(self.iprange, self.name)
            node_workloads = {}
            for net_workload in self.network_workloads:
                node_workloads[net_workload.info.node_id] = net_workload
            network.network_resources = list(node_workloads.values())  # add only latest network resource for each node
            j.sals.zos.network.add_node(network, node.node_id, str(subnet), pool_id)
            return network

    def add_access(self, node_id=None, use_ipv4=True):
        node_id = node_id or self.network_workloads[0].info.node_id
        used_ip_ranges = set()
        for workload in self.network_workloads:
            used_ip_ranges.add(workload.iprange)
            for peer in workload.peers:
                used_ip_ranges.add(peer.iprange)
        else:
            network_range = netaddr.IPNetwork(self.iprange)
            for idx, subnet in enumerate(network_range.subnet(24)):
                if str(subnet) not in used_ip_ranges:
                    break
            else:
                raise StopChatFlow("Failed to find free network")
        network = j.sals.zos.network.create(self.iprange, self.name)
        node_workloads = {}
        for net_workload in self.network_workloads:
            node_workloads[net_workload.info.node_id] = net_workload
        network.network_resources = list(node_workloads.values())  # add only latest network resource for each node
        wg_quick = j.sals.zos.network.add_access(network, node_id, str(subnet), ipv4=use_ipv4)
        return network, wg_quick

    def delete_access(self, ip_range, node_id=None):
        node_id = node_id or self.network_workloads[0].info.node_id
        node_workloads = {}
        for net_workload in self.network_workloads:
            node_workloads[net_workload.info.node_id] = net_workload
        node_ranges = []
        for workload in node_workloads.values():
            node_ranges.append(workload.iprange)
        if ip_range in node_ranges:
            raise StopChatFlow("Can't delete a zos node peer")
        workload = node_workloads[node_id]
        new_peers = []
        # remove the range from access node
        for peer in workload.peers:
            if peer.iprange != ip_range:
                new_peers.append(peer)
        workload.peers = new_peers
        # remove the range from allowed ips on all nodes
        node_range = node_workloads[node_id].iprange
        routing_range = wg_routing_ip(ip_range)
        for network_resource in node_workloads.values():
            for peer in network_resource.peers:
                if node_range == peer.iprange:
                    if ip_range in peer.allowed_iprange:
                        peer.allowed_iprange.remove(ip_range)
                    if routing_range in peer.allowed_iprange:
                        peer.allowed_iprange.remove(routing_range)

        network = j.sals.zos.network.create(self.iprange, self.name)
        network.network_resources = list(node_workloads.values())
        return network

    def get_node_range(self, node):
        for workload in self.network_workloads:
            if workload.info.next_action != NextAction.DEPLOY:
                continue
            if workload.info.node_id == node.node_id:
                return workload.iprange
        raise StopChatFlow(f"Node {node.node_id} is not part of network")

    def copy(self):
        return NetworkView(self.name)

    def get_node_free_ips(self, node):
        ip_range = self.get_node_range(node)
        freeips = []
        hosts = netaddr.IPNetwork(ip_range).iter_hosts()
        next(hosts)  # skip ip used by node
        for host in hosts:
            ip = str(host)
            if ip not in self.used_ips:
                freeips.append(ip)
        return freeips

    def get_free_ip(self, node):
        ip_range = self.get_node_range(node)
        hosts = netaddr.IPNetwork(ip_range).iter_hosts()
        next(hosts)  # skip ip used by node
        for host in hosts:
            ip = str(host)
            if ip not in self.used_ips:
                self.used_ips.append(ip)
                return ip
        return None


class ChatflowDeployer:
    def __init__(self):
        self.workloads = defaultdict(
            lambda: defaultdict(lambda: defaultdict(list))
        )  # Next Action: workload_type: pool_id: [workloads]
        self._explorer = j.core.identity.me.explorer

    def load_user_workloads(self, next_action=NextAction.DEPLOY):
        all_workloads = j.sals.zos.workloads.list(j.core.identity.me.tid, next_action)
        self.workloads.pop(next_action, None)
        for workload in all_workloads:
            if workload.info.metadata:
                workload.info.metadata = self.decrypt_metadata(workload.info.metadata)
            self.workloads[workload.info.next_action][workload.info.workload_type][workload.info.pool_id].append(
                workload
            )

    def decrypt_metadata(self, encrypted_metadata):
        try:
            pk = j.core.identity.me.nacl.signing_key.verify_key.to_curve25519_public_key()
            sk = j.core.identity.me.nacl.signing_key.to_curve25519_private_key()
            box = Box(sk, pk)
            return box.decrypt(base64.b85decode(encrypted_metadata.encode())).decode()
        except:
            return "{}"

    def list_networks(self, next_action=NextAction.DEPLOY, sync=True):
        if sync:
            self.load_user_workloads(next_action=next_action)
        networks = {}  # name: last child network resource
        for pool_id in self.workloads[next_action][Type.Network_resource]:
            for workload in self.workloads[next_action][Type.Network_resource][pool_id]:
                networks[workload.name] = workload
        all_workloads = []
        for pools_workloads in self.workloads[next_action].values():
            for pool_id, workload_list in pools_workloads.items():
                all_workloads += workload_list
        network_views = {}
        for network_name in networks:
            network_views[network_name] = NetworkView(network_name, all_workloads)
        return network_views

    def create_pool(self, bot):
        form = bot.new_form()
        cu = form.int_ask("Please specify the required CU")
        su = form.int_ask("Please specify the required SU")
        currencies = form.single_choice("Please choose the currency", ["TFT", "FreeTFT", "TFTA"])
        form.ask()
        cu = cu.value
        su = su.value
        currencies = [currencies.value]
        all_farms = self._explorer.farms.list()
        available_farms = {}
        for farm in all_farms:
            res = self.check_farm_capacity(farm.name, currencies, cru=None, sru=None)
            available = res[0]
            resources = res[1:]
            if available:
                available_farms[farm.name] = resources
        farm_messages = {}
        for farm in available_farms:
            resources = available_farms[farm]
            farm_messages[
                f"{farm} cru: {resources[0]} sru: {resources[1]} hru: {resources[2]} mru {resources[3]}"
            ] = farm
        selected_farm = bot.single_choice("Please choose a farm", list(farm_messages.keys()))
        farm = farm_messages[selected_farm]
        try:
            pool_info = j.sals.zos.pools.create(cu, su, farm, currencies)
        except Exception as e:
            raise StopChatFlow(f"failed to reserve pool.\n{str(e)}")
        self.show_payment(pool_info, bot)
        return pool_info

    def check_farm_capacity(self, farm_name, currencies=[], sru=None, cru=None, mru=None, hru=None):
        farm_nodes = j.sals.zos.nodes_finder.nodes_search(farm_name=farm_name)
        available_cru = 0
        available_sru = 0
        available_mru = 0
        available_hru = 0
        for node in farm_nodes:
            if "FreeTFT" in currencies and not node.free_to_use:
                continue
            if not j.sals.zos.nodes_finder.filter_is_up(node):
                continue
            available_cru += node.total_resources.cru - node.used_resources.cru
            available_sru += node.total_resources.sru - node.used_resources.sru
            available_mru += node.total_resources.mru - node.used_resources.mru
            available_hru += node.total_resources.hru - node.used_resources.hru
        if sru and available_sru < sru:
            return False, available_cru, available_sru, available_mru, available_hru
        if cru and available_cru < cru:
            return False, available_cru, available_sru, available_mru, available_hru
        if mru and available_mru < mru:
            return False, available_cru, available_sru, available_mru, available_hru
        if hru and available_hru < hru:
            return False, available_cru, available_sru, available_mru, available_hru
        return True, available_cru, available_sru, available_mru, available_hru

    def show_payment(self, pool, bot):
        escrow_info = pool.escrow_information
        resv_id = pool.reservation_id
        escrow_address = escrow_info.address
        escrow_asset = escrow_info.asset
        total_amount = escrow_info.amount
        total_amount_dec = Decimal(total_amount) / Decimal(1e7)
        total_amount = "{0:f}".format(total_amount_dec)

        wallets = j.sals.reservation_chatflow.reservation_chatflow.list_wallets()
        wallet_names = []
        for w in wallets.keys():
            wallet_names.append(w)
        wallet_names.append("3bot app")
        message = f"""
        Billing details:
        <h4> Wallet address: </h4>  {escrow_address} \n
        <h4> Currency: </h4>  {escrow_asset} \n
        <h4> Choose a wallet name to use for payment or proceed with payment through 3bot app </h4>
        """
        result = bot.single_choice(message, wallet_names, html=True)
        if result == "3bot app":
            qr_code = (
                f"{escrow_asset.split(':')[0]}:{escrow_address}?amount={total_amount}&message=p-{resv_id}&sender=me"
            )
            msg_text = f"""
            <h3> Please make your payment </h3>
            Scan the QR code with your application (do not change the message) or enter the information below manually and proceed with the payment. Make sure to add the reservationid as memo_text.

            <h4> Wallet address: </h4>  {escrow_address} \n
            <h4> Currency: </h4>  {escrow_asset} \n
            <h4> Reservation id: </h4>  p-{resv_id} \n
            <h4> Total Amount: </h4> {total_amount} \n
            """
            bot.qrcode_show(data=qr_code, msg=msg_text, scale=4, update=True, html=True)
        else:
            wallet = wallets[result]
            wallet.transfer(
                destination_address=escrow_address, amount=total_amount, asset=escrow_asset, memo_text=f"p-{resv_id}",
            )

    def extend_pool(self, bot, pool_id):
        form = bot.new_form()
        cu = form.int_ask("Please specify the required CU")
        su = form.int_ask("Please specify the required SU")
        currencies = form.single_choice("Please choose the currency", ["TFT", "FreeTFT", "TFTA"])
        form.ask()
        cu = cu.value
        su = su.value
        currencies = [currencies.value]
        try:
            pool_info = j.sals.zos.pools.extend(pool_id, cu, su, currencies=currencies)
        except Exception as e:
            raise StopChatFlow(f"failed to extend pool.\n{str(e)}")
        self.show_payment(pool_info, bot)
        return pool_info

    def list_pools(self, cu=None, su=None):
        all_pools = j.sals.zos.pools.list()
        available_pools = {}
        for pool in all_pools:
            res = self.check_pool_capacity(pool, cu, su)
            available = res[0]
            if available:
                resources = res[1:]
                available_pools[pool.pool_id] = resources
        return available_pools

    def check_pool_capacity(self, pool, cu=None, su=None):
        available_su = pool.sus - pool.active_su
        available_cu = pool.cus - pool.active_cu
        if pool.empty_at < 0:
            return False, 0, 0
        if cu and available_cu < cu:
            return False, available_cu, available_su
        if su and available_su < su:
            return False, available_cu, available_su
        return True, available_cu, available_su

    def select_pool(self, bot, cu=None, su=None, available_pools=None, workload_name=None):
        available_pools = available_pools or self.list_pools(cu, su)
        if not available_pools:
            raise StopChatFlow("no available pools")
        pool_messages = {}
        for pool in available_pools:
            pool_messages[f"Pool: {pool} cu: {available_pools[pool][0]} su: {available_pools[pool][1]}"] = pool
        msg = "Please select a pool"
        if workload_name:
            msg += f" for {workload_name}"
        pool = bot.single_choice(msg, list(pool_messages.keys()))
        return pool_messages[pool]

    def get_pool_farm_id(self, pool_id):
        pool = j.sals.zos.pools.get(pool_id)
        node_id = pool.node_ids[0]
        node = self._explorer.nodes.get(node_id)
        farm_id = node.farm_id
        return farm_id

    def check_pool_capacity(self, pool, cu=None, su=None):
        """
        pool: pool schema object
        """
        available_su = pool.sus - pool.active_su
        available_cu = pool.cus - pool.active_cu
        if pool.empty_at < 0:
            return False, 0, 0
        if cu and available_cu < cu:
            return False, available_cu, available_su
        if su and available_su < su:
            return False, available_cu, available_su
        return True, available_cu, available_su

    def ask_name(self, bot):
        name = bot.string_ask("Please enter a name for you workload", required=True, field="name")
        return name

    def ask_ipv6(self, bot, workload_name=None):
        workload_name = workload_name or "your workload"
        ipv6 = bot.single_choice(
            f"Do you want to assign a global IPv6 address to {workload_name}?", options=["YES", "NO"], default="NO"
        )
        return ipv6 == "YES"

    def encrypt_metadata(self, metadata):
        if isinstance(metadata, dict):
            metadata = j.data.serializers.json.dumps(metadata)
        pk = j.core.identity.me.nacl.signing_key.verify_key.to_curve25519_public_key()
        sk = j.core.identity.me.nacl.signing_key.to_curve25519_private_key()
        box = Box(sk, pk)
        encrypted_metadata = base64.b85encode(box.encrypt(metadata.encode())).decode()
        return encrypted_metadata

    def deploy_network(self, name, access_node, ip_range, ip_version, pool_id, **metadata):
        network = j.sals.zos.network.create(ip_range, name)
        node_subnets = netaddr.IPNetwork(ip_range).subnet(24)
        network_config = dict()
        use_ipv4 = ip_version == "IPv4"

        j.sals.zos.network.add_node(network, access_node.node_id, str(next(node_subnets)), pool_id)
        wg_quick = j.sals.zos.network.add_access(network, access_node.node_id, str(next(node_subnets)), ipv4=use_ipv4)

        network_config["wg"] = wg_quick
        j.sals.fs.mkdir(f"{j.core.dirs.CFGDIR}/wireguard/")
        j.sals.fs.write_file(f"{j.core.dirs.CFGDIR}/{name}.conf", f"{wg_quick}")

        ids = []
        parent_id = None
        for workload in network.network_resources:
            workload.info.description = j.data.serializers.json.dumps({"parent_id": parent_id})
            metadata["parent_network"] = parent_id
            workload.metadata = self.encrypt_metadata(metadata)
            ids.append(j.sals.zos.workloads.deploy(workload))
            parent_id = ids[-1]
        network_config["ids"] = ids
        network_config["rid"] = ids[0]
        return network_config

    def add_access(self, network_name, network_view=None, node_id=None, use_ipv4=True):
        network_view = network_view or NetworkView(network_name)
        network, wg = network_view.add_access(node_id, use_ipv4)
        result = {"ids": [], "wg": wg}
        for resource in network.network_resources:
            result["ids"].append(j.sals.zos.workloads.deploy(resource))
        result["rid"] = result["ids"][0]
        return result

    def wait_workload(self, workload_id, bot=None):
        expiration_provisioning = j.data.time.now().timestamp + 15 * 60
        while True:
            workload = j.sals.zos.workloads.get(workload_id)
            remaning_time = j.data.time.get(expiration_provisioning).humanize(granularity=["minute", "second"])
            if bot:
                deploying_message = f"""
# Deploying...\n
Deployment will be cancelled if it is not successful in {remaning_time}
                """
                bot.md_show_update(deploying_message, md=True)
            if workload.info.result.workload_id:
                return workload.info.result.state.value == 1
            if expiration_provisioning < j.data.time.get().timestamp:
                j.sal.chatflow_solutions.cancel_solution([workload_id])
                raise StopChatFlow(f"Workload {workload_id} failed to deploy in time")
            gevent.sleep(1)

    def add_network_node(self, name, node, pool_id, network_view=None, **metadata):
        if not network_view:
            network_view = NetworkView(name)
        network = network_view.add_node(node, pool_id)
        if not network:
            return
        parent_id = network_view.network_workloads[-1].id
        ids = []
        node_workloads = {}
        # deploy only latest resource generated by zos sal for each node
        for workload in network.network_resources:
            node_workloads[workload.info.node_id] = workload
        for workload in node_workloads.values():
            workload.info.description = j.data.serializers.json.dumps({"parent_id": parent_id})
            metadata["parent_network"] = parent_id
            workload.metadata = self.encrypt_metadata(metadata)
            ids.append(j.sals.zos.workloads.deploy(workload))
            parent_id = ids[-1]
        return {"ids": ids, "rid": ids[0]}

    def select_network(self, bot):
        network_views = self.list_networks()
        if not network_views:
            raise StopChatFlow(f"You don't have any deployed network.")
        network_name = bot.single_choice("Please select a network", list(network_views.keys()))
        return network_views[network_name]

    def deploy_volume(self, pool_id, node_id, size, volume_type=DiskType.SSD, **metadata):
        volume = j.sals.zos.volume.create(node_id, pool_id, size, volume_type)
        if metadata:
            volume.info.metadata = self.encrypt_metadata(metadata)
        return j.sals.zos.workloads.deploy(volume)

    def deploy_container(
        self,
        pool_id,
        node_id,
        network_name,
        ip_address,
        flist,
        env=None,
        cpu=1,
        memory=1024,
        disk_size=256,
        disk_type=DiskType.SSD,
        entrypoint="",
        interactive=False,
        secret_env=None,
        volumes=None,
        log_config=None,
        public_ipv6=False,
        **metadata,
    ):
        """
        volumes: dict {"mountpoint (/)": volume_id}
        log_Config: dict. keys ("channel_type", "channel_host", "channel_port", "channel_name")
        """
        env = env or {}
        encrypted_secret_env = {}
        if secret_env:
            for key, val in secret_env.items():
                encrypted_secret_env[key] = j.sals.zos.container.encrypt_secret(node_id, val)
        container = j.sals.zos.container.create(
            node_id,
            network_name,
            ip_address,
            flist,
            pool_id,
            env,
            cpu,
            memory,
            disk_size,
            disk_type,
            entrypoint,
            interactive,
            encrypted_secret_env,
            public_ipv6=public_ipv6,
        )
        if volumes:
            for mount_point, vol_id in volumes.items():
                j.sals.zos.volume.attach_existing(container, f"{vol_id}-1", mount_point)
        if metadata:
            container.info.metadata = self.encrypt_metadata(metadata)
        if log_config:
            j.sals.zos.container.add_logs(container, **log_config)
        return j.sals.zos.workloads.deploy(container)

    def ask_container_resources(
        self,
        bot,
        cpu=True,
        memory=True,
        disk_size=True,
        disk_type=False,
        default_cpu=1,
        default_memory=1024,
        default_disk_size=256,
        default_disk_type="SSD",
    ):
        form = bot.new_form()
        if cpu:
            cpu_answer = form.int_ask("Please specify how many cpus", default=default_cpu)
        if memory:
            memory_answer = form.int_ask("Please specify how much memory", default=default_memory)
        if disk_size:
            disk_size_answer = form.int_ask("Please specify the size of root filesystem", default=default_disk_size)
        if disk_type:
            disk_type_answer = form.single_choice(
                "Please choose the root filesystem disktype", ["SSD", "HDD"], default=default_disk_type
            )
        form.ask()
        resources = {}
        if cpu:
            resources["cpu"] = cpu_answer.value
        if memory:
            resources["memory"] = memory_answer.value
        if disk_size:
            resources["disk_size"] = disk_size_answer.value
        if disk_type:
            resources["disk_type"] = DiskType[disk_type_answer.value]
        return resources

    def ask_container_logs(self, bot, solution_name=None):
        logs_config = {}
        form = bot.new_form()
        channel_type = form.string_ask("Please add the channel type", default="redis", required=True)
        channel_host = form.string_ask("Please add the IP address where the logs will be output to", required=True)
        channel_port = form.int_ask("Please add the port available where the logs will be output to", required=True)
        channel_name = form.string_ask(
            "Please add the channel name to be used. The channels will be in the form NAME-stdout and NAME-stderr",
            default=solution_name,
            required=True,
        )
        form.ask()
        logs_config["channel_type"] = channel_type.value
        logs_config["channel_host"] = channel_host.value
        logs_config["channel_port"] = channel_port.value
        logs_config["channel_name"] = channel_name.value
        return logs_config

    def schedule_container(self, pool_id, cru=None, sru=None, mru=None, hru=None, ip_version=None):
        farm_id = self.get_pool_farm_id(pool_id)
        farm_name = self._explorer.farms.get(farm_id).name
        query = {"cru": cru, "sru": sru, "mru": mru, "hru": hru, "ip_version": ip_version}
        return j.sals.reservation_chatflow.reservation_chatflow.get_nodes(1, farm_names=[farm_name], **query)[0]

    def ask_container_placement(
        self,
        bot,
        pool_id,
        cru=None,
        sru=None,
        mru=None,
        hru=None,
        ip_version=None,
        free_to_use=False,
        workload_name=None,
    ):
        if not workload_name:
            workload_name = "your workload"
        automatic_choice = bot.single_choice(
            f"Do you want to automatically select a node for deployment or manually for {workload_name}?",
            ["YES", "NO"],
            default="YES",
        )
        if automatic_choice == "YES":
            return None
        farm_id = self.get_pool_farm_id(pool_id)
        nodes = j.sals.zos.nodes_finder.nodes_by_capacity(farm_id=farm_id, cru=cru, sru=sru, mru=mru, hru=hru)
        nodes = list(nodes)
        nodes = j.sals.reservation_chatflow.reservation_chatflow.filter_nodes(nodes, free_to_use, ip_version)
        if not nodes:
            raise StopChatFlow("Failed to find resources for this reservation")
        node_messages = {node.node_id: node for node in nodes}
        node_id = bot.drop_down_choice(
            f"Please choose the node you want to deploy {workload_name} on", list(node_messages.keys())
        )
        return node_messages[node_id]

    def calculate_capacity_units(self, cru=0, mru=0, sru=0, hru=0):
        """
        return cu, su
        """
        cu = min(cru * 4, (mru - 1) / 4)
        su = hru / 1000 / 1.2 + sru / 100 / 1.2
        return cu, su

    def get_network_view(self, network_name, workloads=None):
        return NetworkView(network_name, workloads)

    def delegate_domain(self, pool_id, gateway_id, domain_name, **metadata):
        domain_delegate = j.sals.zos.gateway.delegate_domain(gateway_id, domain_name, pool_id)
        if metadata:
            domain_delegate.info.metadata = self.encrypt_metadata(metadata)
        return j.sals.zos.workloads.deploy(domain_delegate)

    def deploy_kubernetes_master(
        self, pool_id, node_id, network_name, cluster_secret, ssh_keys, ip_address, size=1, **metadata
    ):
        master = j.sals.zos.kubernetes.add_master(
            node_id, network_name, cluster_secret, ip_address, size, ssh_keys, pool_id
        )
        master.info.description = j.data.serializers.json.dumps({"role": "master"})
        if metadata:
            master.info.metadata = self.encrypt_metadata(metadata)
        return j.sals.zos.workloads.deploy(master)

    def deploy_kubernetes_worker(
        self, pool_id, node_id, network_name, cluster_secret, ssh_keys, ip_address, master_ip, size=1, **metadata
    ):
        worker = j.sals.zos.kubernetes.add_worker(
            node_id, network_name, cluster_secret, ip_address, size, master_ip, ssh_keys, pool_id
        )
        worker.info.description = j.data.serializers.json.dumps({"role": "worker"})
        if metadata:
            worker.info.metadata = self.encrypt_metadata(metadata)
        return j.sals.zos.workloads.deploy(worker)

    def deploy_kubernetes_cluster(
        self,
        pool_id,
        node_ids,
        network_name,
        cluster_secret,
        ssh_keys,
        size=1,
        ip_addresses=None,
        slave_pool_ids=None,
        **metadata,
    ):
        """
        deplou k8s cluster with the same number of nodes as specifed in node_ids

        Args:
            pool_id: this one is always used for master.
            node_ids: list() of node ids to deploy on. first node_id is used for master reservation
            ip_addresses: if specified it will be mapped 1-1 with node_ids for workloads. if not specified it will choose any free_ip from the node
            slave_pool_ids: if specified, k8s workers will deployed on each of these pools respectively. if empty it will use the master pool_id

        Return:
            list: [{"node_id": "ip_address"}, ...] first dict is master's result
        """
        slave_pool_ids = slave_pool_ids or ([pool_id] * (len(node_ids) - 1))
        pool_ids = [pool_id] + slave_pool_ids
        result = []  # [{"node_id": id,  "ip_address": ip, "reservation_id": 16}] first dict is master's result
        if ip_addresses and len(ip_addresses) != len(node_ids):
            raise StopChatFlow("length of ips != node_ids")

        if not ip_addresses:
            # get free_ips for the nodes
            ip_addresses = []
            for i in range(len(node_ids)):
                node_id = node_ids[i]
                pool_id = pool_ids[i]
                node = self._explorer.nodes.get(node_id)
                res = self.add_network_node(network_name, node, pool_id)
                if res:
                    for wid in res["ids"]:
                        success = self.wait_workload(wid)
                        if not success:
                            raise StopChatFlow(f"Failed to add node {node.node_id} to network {wid}")
                network_view = NetworkView(network_name)
                address = network_view.get_free_ip(node)
                if not address:
                    raise StopChatFlow(f"No free IPs for network {network_name} on the specifed node {node_id}")
                ip_addresses.append(address)

        # deploy_master
        master_ip = ip_addresses[0]
        master_resv_id = self.deploy_kubernetes_master(
            pool_ids[0], node_ids[0], network_name, cluster_secret, ssh_keys, master_ip, size, **metadata
        )
        result.append({"node_id": node_ids[0], "ip_address": master_ip, "reservation_id": master_resv_id})
        for i in range(1, len(node_ids)):
            node_id = node_ids[i]
            pool_id = pool_ids[i]
            ip_address = ip_addresses[i]
            resv_id = self.deploy_kubernetes_worker(
                pool_id, node_id, network_name, cluster_secret, ssh_keys, ip_address, master_ip, size, **metadata
            )
            result.append({"node_id": node_id, "ip_address": ip_address, "reservation_id": resv_id})
        return result

    def ask_multi_pool_placement(
        self, bot, number_of_nodes, resource_query_list=None, pool_ids=None, workload_names=None
    ):
        """
        Ask and schedule workloads accross multiple pools

        Args:
            bot: chatflow object
            number_of_nodes: number of required nodes for deployment
            resource_query_list: list of query dicts {"cru": 1, "sru": 2, "mru": 1, "hru": 1}. if specified it must be same length as number_of_nodes
            pool_ids: if specfied it will limit the pools shown in the chatflow to only these pools
            workload_names: if specified they will shown when asking the user for node selection for each workload. if specified it must be same length as number_of_nodes

        Returns:
            ([], []): first list contains the selected node objects. second list contains selected pool ids
        """
        resource_query_list = resource_query_list or [dict()] * number_of_nodes
        workload_names = workload_names or [None] * number_of_nodes
        if len(resource_query_list) != number_of_nodes:
            raise StopChatFlow("resource query_list must be same length as number of nodes")
        if len(workload_names) != number_of_nodes:
            raise StopChatFlow("workload_names must be same length as number of nodes")

        pools = self.list_pools()
        if pool_ids:
            filtered_pools = {}
            for pool_id in pools:
                if pool_id in pool_ids:
                    filtered_pools[pool_id] = pools[pool_id]
            pools = filtered_pools
        selected_nodes = []
        selected_pool_ids = []
        for i in range(number_of_nodes):
            cu, su = self.calculate_capacity_units(**resource_query_list[i])
            pool_choices = {}
            for p in pools:
                if pools[p][0] < cu or pools[p][1] < su:
                    continue
                pool_choices[p] = pools[p]
            pool_id = self.select_pool(bot, available_pools=pool_choices, workload_name=workload_names[i], cu=cu, su=su)
            node = self.ask_container_placement(bot, pool_id, workload_name=workload_names[i], **resource_query_list[i])
            if not node:
                node = self.schedule_container(pool_id, **resource_query_list[i])
            selected_nodes.append(node)
            selected_pool_ids.append(pool_id)
        return selected_nodes, selected_pool_ids

    def list_pool_gateways(self, pool_id):
        """
        return dict of gateways where keys are descriptive string of each gateway
        """
        farm_id = self.get_pool_farm_id(pool_id)
        gateways = self._explorer.gateway.list(farm_id=farm_id)
        if not gateways:
            raise StopChatFlow(f"no available gateways in pool {pool_id} farm: {farm_id}")
        result = {}
        for g in gateways:
            if not g.dns_nameserver:
                continue
            result[f"{g.dns_nameserver[0]} {g.location.continent} {g.location.country} {g.node_id}"] = g
        return result

    def list_all_gateways(self, pool_ids=None):
        """
        Args:
            pool_ids: if specified it will only list gateways inside these pools

        Returns:
            dict: {"gateway_message": {"gateway": g, "pool": pool},}
        """
        all_gateways = filter(j.sals.zos.nodes_finder.filter_is_up, self._explorer.gateway.list())
        if not all_gateways:
            raise StopChatFlow(f"no available gateways")
        all_pools = j.sals.zos.pools.list()
        available_node_ids = {}  # node_id: pool
        if pool_ids:
            for pool in all_pools:
                if pool.pool_id in pool_ids:
                    available_node_ids.update({node_id: pool for node_id in pool.node_ids})
        else:
            for pool in all_pools:
                available_node_ids.update({node_id: pool for node_id in pool.node_ids})
        result = {}
        for gateway in all_gateways:
            if gateway.node_id in available_node_ids:
                if len(gateway.dns_nameserver) < 1:
                    continue
                pool = available_node_ids[gateway.node_id]
                message = f"Pool: {pool.pool_id} {gateway.dns_nameserver[0]} {gateway.location.continent} {gateway.location.country} {gateway.node_id}"
                result[message] = {"gateway": gateway, "pool": pool}
        if not result:
            raise StopChatFlow(f"no available gateways")
        return result

    def select_gateway(self, bot, pool_ids=None):
        """
        Args:
            pool_ids: if specified it will only list gateways inside these pools

        Returns:
            gateway, pool_objects
        """
        gateways = self.list_all_gateways(pool_ids)

        selected = bot.single_choice("Please select a gateway", list(gateways.keys()))
        return gateways[selected]["gateway"], gateways[selected]["pool"]

    def create_ipv6_gateway(self, gateway_id, pool_id, public_key, **metadata):
        if isinstance(public_key, bytes):
            public_key = public_key.decode()
        workload = j.sals.zos.gateway.gateway_4to6(gateway_id, public_key, pool_id)
        if metadata:
            workload.info.metadata = self.encrypt_metadata(metadata)
        return j.sals.zos.workloads.deploy(workload)

    def deploy_zdb(self, pool_id, node_id, size, mode, password, disk_type="SSD", public=False, **metadata):
        workload = j.sals.zos.zdb.create(node_id, size, mode, password, pool_id, disk_type, public)
        if metadata:
            workload.info.metadata = self.encrypt_metadata(metadata)
        return j.sals.zos.workloads.deploy(workload)

    def create_subdomain(self, pool_id, gateway_id, subdomain, addresses=None, **metadata):
        """
        creates an A record pointing to the specified addresses
        if no addresses are specified, the record will point the gateway IP address (used for exposing solutions)
        """
        if not addresses:
            gateway = self._explorer.gateway.get(gateway_id)
            addresses = [j.sals.nettools.get_host_by_name(ns) for ns in gateway.dns_nameserver]
        workload = j.sals.zos.gateway.sub_domain(gateway_id, subdomain, addresses, pool_id)
        if metadata:
            workload.info.metadata = self.encrypt_metadata(metadata)
        return j.sals.zos.workloads.deploy(workload)

    def create_proxy(self, pool_id, gateway_id, domain_name, trc_secret, **metadata):
        """
        creates a reverse tunnel on the gateway node
        """
        workload = j.sals.zos.gateway.tcp_proxy_reverse(gateway_id, domain_name, trc_secret, pool_id)
        if metadata:
            workload.info.metadata = self.encrypt_metadata(metadata)
        return j.sals.zos.workloads.deploy(workload)

    def expose_address(
        self,
        pool_id,
        gateway_id,
        network_name,
        local_ip,
        port,
        tls_port,
        trc_secret,
        node_id=None,
        reserve_proxy=False,
        domain_name=None,
        bot=None,
        **metadata,
    ):
        gateway = self._explorer.gateway.get(gateway_id)

        if reserve_proxy:
            if not domain_name:
                raise StopChatFlow("you must pass domain_name when you ise reserv_proxy")
            resv_id = self.create_proxy(
                pool_id=pool_id, gateway_id=gateway_id, domain_name=domain_name, trc_secret=trc_secret, **metadata,
            )

        remote = f"{gateway.dns_nameserver[0]}:{gateway.tcp_router_port}"
        secret_env = {"TRC_SECRET": trc_secret}
        entry_point = f"/bin/trc -local {local_ip}:{port} -local-tls {local_ip}:{tls_port} -remote {remote}"
        if not node_id:
            node = self.schedule_container(pool_id=pool_id, cru=1, mru=1, hru=1)
            node_id = node.node_id
        else:
            node = self._explorer.nodes.get(node_id)

        res = self.add_network_node(network_name, node, pool_id)
        if res:
            for wid in res["ids"]:
                success = self.wait_workload(wid, bot)
                if not success:
                    if reserve_proxy:
                        j.sals.reservation_chatflows.solutions.cancel_solution([wid])
                    raise StopChatFlow(f"Failed to add node {node.node_id} to network {wid}")
        network_view = NetworkView(network_name)
        ip_address = network_view.get_free_ip(node)

        resv_id = self.deploy_container(
            pool_id=pool_id,
            node_id=node_id,
            network_name=network_name,
            ip_address=ip_address,
            flist="https://hub.grid.tf/tf-official-apps/tcprouter:latest.flist",
            disk_type=DiskType.HDD,
            entrypoint=entry_point,
            secret_env=secret_env,
            public_ipv6=False,
            **metadata,
        )
        return resv_id

    def deploy_minio_zdb(
        self,
        pool_id,
        password,
        node_ids=None,
        zdb_no=None,
        disk_type=DiskType.HDD,
        disk_size=10,
        pool_ids=None,
        **metadata,
    ):
        """
        deploy zdb workloads on the specified node_ids if specified or deploy workloads as specifdied by the zdb_no
        Args:
            pool_id: used to deploy all workloads in this pool (overriden when pool_ids is specified)
            node_ids: if specified, it will be used for deployment of workloads.
            pool_ids: if specified, zdb workloads will be
            zdb_no: if specified and no node_ids, it will automatically schedule zdb workloads matching pool config

        Returns:
            []: list of workload ids deployed
        """
        node_ids = node_ids or []
        if not (zdb_no or node_ids):
            raise StopChatFlow("you must pass at least one of zdb_no or node_ids")

        if node_ids:
            pool_ids = pool_ids or [pool_id] * len(node_ids)
        else:
            pool_ids = pool_ids or [pool_id] * zdb_no

        if len(pool_ids) != len(node_ids):
            raise StopChatFlow("pool_ids must be same length as node_ids")

        if not node_ids and zdb_no:
            query = {}
            if disk_type == DiskType.SSD:
                query["sru"] = disk_size
            else:
                query["hru"] = disk_size
            for pool_id in pool_ids:
                farm_id = self.get_pool_farm_id(pool_id)
                node = j.sals.reservation_chatflow.reservation_chatflow.nodes_get(
                    farm_id=farm_id, number_of_nodes=1, **query
                )[0]
                node_ids.append(node.node_id)

        result = []
        for i in range(len(node_ids)):
            node_id = node_ids[i]
            pool_id = pool_ids[i]
            resv_id = self.deploy_zdb(
                pool_id=pool_id,
                node_id=node_id,
                size=disk_size,
                mode=Mode.Seq,
                password=password,
                disk_type=disk_type,
                **metadata,
            )
            result.append(resv_id)
        return result

    def deploy_minio_containers(
        self,
        pool_id,
        network_name,
        minio_nodes,
        minio_ip_addresses,
        zdb_configs,
        ak,
        sk,
        ssh_key,
        cpu,
        memory,
        data,
        parity,
        disk_type=DiskType.SSD,
        disk_size=10,
        log_config=None,
        mode="Single",
        bot=None,
        public_ipv6=False,
        secondary_pool_id=None,
        **metadata,
    ):
        secondary_pool_id = secondary_pool_id or pool_id
        secret_env = {}
        if mode == "Master/Slave":
            secret_env["TLOG"] = zdb_configs.pop(-1)
        shards = ",".join(zdb_configs)
        secret_env["SHARDS"] = shards
        secret_env["SECRET_KEY"] = sk
        env = {
            "DATA": str(data),
            "PARITY": str(parity),
            "ACCESS_KEY": ak,
            "SSH_KEY": ssh_key,
            "MINIO_PROMETHEUS_AUTH_TYPE": "public",
        }
        result = []
        master_volume_id = self.deploy_volume(pool_id, minio_nodes[0], disk_size, disk_type, **metadata)
        success = self.wait_workload(master_volume_id, bot)
        if not success:
            raise StopChatFlow(
                f"Failed to create volume {master_volume_id} for minio container on node {minio_nodes[0]}"
            )
        master_cont_id = self.deploy_container(
            pool_id=pool_id,
            node_id=minio_nodes[0],
            network_name=network_name,
            ip_address=minio_ip_addresses[0],
            env=env,
            cpu=cpu,
            memory=memory,
            secret_env=secret_env,
            log_config=log_config,
            volumes={"/data": master_volume_id},
            public_ipv6=public_ipv6,
            flist="https://hub.grid.tf/tf-official-apps/minio:latest.flist",
            **metadata,
        )
        result.append(master_cont_id)
        if mode == "Master/Slave":
            secret_env["MASTER"] = secret_env.pop("TLOG")
            slave_volume_id = self.deploy_volume(pool_id, minio_nodes[1], disk_size, disk_type, **metadata)
            success = self.wait_workload(slave_volume_id, bot)
            if not success:
                raise StopChatFlow(
                    f"Failed to create volume {slave_volume_id} for minio container on node {minio_nodes[1]}"
                )
            slave_cont_id = self.deploy_container(
                pool_id=secondary_pool_id,
                node_id=minio_nodes[1],
                network_name=network_name,
                ip_address=minio_ip_addresses[1],
                env=env,
                cpu=cpu,
                memory=memory,
                secret_env=secret_env,
                log_config=log_config,
                volumes={"/data": slave_volume_id},
                public_ipv6=public_ipv6,
                flist="https://hub.grid.tf/tf-official-apps/minio:latest.flist",
                **metadata,
            )
            result.append(slave_cont_id)
        return result

    def get_zdb_url(self, zdb_id, password):
        workload = j.sals.zos.workloads.get(zdb_id)
        result_json = j.data.serializers.json.loads(workload.info.result.data_json)
        if "IPs" in result_json:
            ip = result_json["IPs"][0]
        else:
            ip = result_json["IP"]
        namespace = result_json["Namespace"]
        port = result_json["Port"]
        url = f"{namespace}:{password}@[{ip}]:{port}"
        return url


deployer = ChatflowDeployer()