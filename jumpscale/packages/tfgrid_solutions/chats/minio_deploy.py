import math
import uuid

from jumpscale.clients.explorer.models import Category, DiskType, Mode
from jumpscale.loader import j
from jumpscale.sals.chatflows.chatflows import GedisChatBot, chatflow_step, StopChatFlow
from jumpscale.sals.reservation_chatflow import solutions, deployer


class MinioDeploy(GedisChatBot):
    steps = [
        "deployment_start",
        "minio_name",
        "setup_type",
        "zdb_storage_type",
        "container_resources",
        "minio_resources",
        "zdb_nodes_selection",
        "minio_nodes_selection",
        "minio_network",
        "access_credentials",
        "container_logs",
        "public_key",
        "ip_selection",
        "ipv6_config",
        "overview",
        "zdb_reservation",
        "minio_reservation",
        "success",
    ]
    title = "Minio"

    @chatflow_step(title="Welcome")
    def deployment_start(self):
        self.solution_id = uuid.uuid4().hex
        self.user_form_data = {}
        self.user_form_data["chatflow"] = "minio"
        self.md_show("# This wizard will help you deploy a minio cluster")
        self.solution_metadata = {}

    @chatflow_step(title="Solution name")
    def minio_name(self):
        valid = False
        while not valid:
            self.solution_name = deployer.ask_name(self)
            minio_solutions = solutions.list_minio_solutions(sync=False)
            valid = True
            for sol in minio_solutions:
                if sol["Name"] == self.solution_name:
                    valid = False
                    self.md_show("The specified solution name already exists. please choose another.")
                    break
                valid = True

    @chatflow_step(title="Setup type")
    def setup_type(self):
        self.mode = self.drop_down_choice(
            "Please choose the type of setup you need. Single setup is the basic setup while master/slave setup includes TLOG use to be able to reconstruct the metadata",
            ["Single", "Master/Slave"],
            required=True,
            default="Single",
        )

    @chatflow_step(title="ZDB Storage")
    def zdb_storage_type(self):
        self.zdb_disk_type = DiskType[
            self.drop_down_choice(
                "Please choose a the type of disk for zdb", ["SSD", "HDD"], required=True, default="SSD"
            )
        ]

    @chatflow_step(title="Container resources")
    def container_resources(self):
        self.minio_cont_resources = deployer.ask_container_resources(self, disk_size=False)

    @chatflow_step(title="Resources for minio")
    def minio_resources(self):
        form = self.new_form()
        data_number = form.int_ask(
            "Please add the number of locations you need. Take care of the ratio between the locations and locations allowed to fail that you will specify next",
            default=2,
        )
        parity = form.int_ask("Please add the number of locations allowed to fail", default=1)
        form.ask()
        self.data = data_number.value
        self.parity = parity.value
        self.zdb_number = self.data + self.parity
        self.minio_number = 1
        if self.mode == "Master/Slave":
            self.minio_number += 1
            self.zdb_number += 1

    @chatflow_step(title="ZDB Nodes")
    def zdb_nodes_selection(self):
        if self.zdb_disk_type == DiskType.SSD:
            queries = [{"sru": 10}] * self.zdb_number
        else:
            queries = [{"hru": 10}] * self.zdb_number
        workload_names = [f"ZDB {i + 1}" for i in range(self.zdb_number)]
        self.zdb_nodes, self.zdb_pool_ids = deployer.ask_multi_pool_placement(
            self, len(queries), queries, workload_names=workload_names
        )

    @chatflow_step(title="Minio Nodes")
    def minio_nodes_selection(self):
        queries = [
            {
                "sru": 10,
                "mru": math.ceil(self.minio_cont_resources["memory"] / 1024),
                "cru": self.minio_cont_resources["cpu"],
            }
        ] * self.minio_number
        workload_names = ["Primary"]
        if self.mode == "Master/Slave":
            workload_names.append("Secondary")
        self.minio_nodes, self.minio_pool_ids = deployer.ask_multi_pool_placement(
            self, len(queries), queries, workload_names=workload_names,
        )

    @chatflow_step(title="Network")
    def minio_network(self):
        self.network_view = deployer.select_network(self)

    @chatflow_step(title="Access credentials")
    def access_credentials(self):
        name = self.user_info()["username"]
        accesskey_string = f"{name.split('.')[0]}"
        secret_string = "secret12345"
        form = self.new_form()
        accesskey = form.string_ask(
            "Please add the key to be used for minio when logging in. Make sure not to lose it",
            default=accesskey_string,
            min_length=3,
        )
        secret = form.string_ask(
            "Please add the secret to be used for minio when logging in to match the previous key. Make sure not to lose it",
            default=secret_string,
            min_length=8,
        )
        form.ask()

        self.ak = accesskey.value
        self.sk = secret.value

    @chatflow_step(title="Container logs")
    def container_logs(self):
        self.container_logs_option = self.single_choice(
            "Do you want to push the container logs (stdout and stderr) onto an external redis channel",
            ["YES", "NO"],
            default="NO",
        )
        if self.container_logs_option == "YES":
            self.log_config = deployer.ask_container_logs(self, self.solution_name)
        else:
            self.log_config = {}

    @chatflow_step(title="Public key")
    def public_key(self):
        public_key_file = self.upload_file(
            """Please add your public ssh key, this will allow you to access the deployed minio container using ssh.
                Just upload the file with the key. (Optional)"""
        ).split("\n")
        if public_key_file:
            self.public_ssh_key = public_key_file[0]
        else:
            self.public_ssh_key = ""

    @chatflow_step(title="Minio container IP")
    def ip_selection(self):
        self.md_show_update("Deploying Network on Nodes....")
        for i in range(len(self.minio_nodes)):
            node = self.minio_nodes[i]
            pool_id = self.minio_pool_ids[i]
            result = deployer.add_network_node(self.network_view.name, node, pool_id, self.network_view, bot=self)
            if not result:
                continue
            for wid in result["ids"]:
                success = deployer.wait_workload(wid)
                if not success:
                    raise StopChatFlow(f"Failed to add node {node.node_id} to network {wid}")
            self.network_view = self.network_view.copy()

        self.ip_addresses = []
        free_ips = self.network_view.get_node_free_ips(self.minio_nodes[0])
        self.ip_addresses.append(
            self.drop_down_choice(
                "Please choose IP Address for Primary container", free_ips, required=True, default=free_ips[0]
            )
        )
        self.network_view.used_ips.append(self.ip_addresses[0])
        if self.mode == "Master/Slave":
            free_ips = self.network_view.get_node_free_ips(self.minio_nodes[1])
            self.ip_addresses.append(
                self.drop_down_choice(
                    "Please choose IP Address for Secondary container", free_ips, required=True, default=free_ips[0]
                )
            )
            self.network_view.used_ips.append(self.ip_addresses[1])

    @chatflow_step(title="Global IPv6 Address")
    def ipv6_config(self):
        self.public_ipv6 = deployer.ask_ipv6(self)

    @chatflow_step(title="Confirmation")
    def overview(self):
        self.metadata = {
            "Solution Name": self.solution_name,
            "Solution Type": "minio",
            "Setup Type": self.mode,
            "Master IP": self.ip_addresses[0],
        }
        self.solution_metadata.update(self.metadata)
        if self.mode == "Master/Slave":
            self.metadata["Slave IP"] = self.ip_addresses[1]
        self.md_show_confirm(self.metadata)

    @chatflow_step(title="Reserve zdb", disable_previous=True)
    def zdb_reservation(self):
        self.password = uuid.uuid4().hex
        self.zdb_result = deployer.deploy_minio_zdb(
            pool_id=self.zdb_pool_ids[0],
            password=self.password,
            node_ids=[n.node_id for n in self.zdb_nodes],
            zdb_no=self.zdb_number,
            pool_ids=self.zdb_pool_ids,
            solution_uuid=self.solution_id,
            **self.solution_metadata,
        )
        for resv_id in self.zdb_result:
            success = deployer.wait_workload(resv_id, self)
            if not success:
                solutions.cancel_solution([resv_id])
                raise StopChatFlow(f"failed to deploy zdb workload {resv_id}")

    @chatflow_step(title="Reserve minio container", disable_previous=True)
    def minio_reservation(self):
        zdb_configs = []
        for zid in self.zdb_result:
            zdb_configs.append(deployer.get_zdb_url(zid, self.password))

        metadata = {
            "name": self.solution_name,
            "form_info": {
                "chatflow": "minio",
                "Solution name": self.solution_name,
                "Master IP": self.ip_addresses[0],
                "ZDB URLS": zdb_configs,
            },
        }
        self.solution_metadata.update(metadata)

        if self.mode == "Master/Slave":
            metadata["form_info"]["Slave IP"] = self.ip_addresses[1]

        self.minio_result = deployer.deploy_minio_containers(
            pool_id=self.minio_pool_ids[0],
            network_name=self.network_view.name,
            minio_nodes=[n.node_id for n in self.minio_nodes],
            minio_ip_addresses=self.ip_addresses,
            zdb_configs=zdb_configs,
            ak=self.ak,
            sk=self.sk,
            ssh_key=self.public_ssh_key,
            cpu=self.minio_cont_resources["cpu"],
            memory=self.minio_cont_resources["memory"],
            data=self.data,
            parity=self.parity,
            disk_size=1,
            log_config=self.log_config,
            mode=self.mode,
            bot=self,
            pool_ids=self.minio_pool_ids,
            solution_uuid=self.solution_id,
            public_ipv6=self.public_ipv6,
            **self.solution_metadata,
        )
        for resv_id in self.minio_result:
            success = deployer.wait_workload(resv_id)
            if not success:
                solutions.cancel_solution([resv_id])
                raise StopChatFlow(f"Failed to deploy Minio container workload {resv_id}")

    @chatflow_step(title="Success", disable_previous=True)
    def success(self):
        res = f"""\
# Minio cluster has been deployed successfully.
Open your browser at [http://{self.ip_addresses[0]}:9000](http://{self.ip_addresses[0]}:9000). It may take a few minutes.
                """
        if self.mode == "Master/Slave":
            res += f"""\
You can access the slave machine at [http://{self.ip_addresses[1]}:9000](http://{self.ip_addresses[1]}:9000)
                """
        self.md_show(res)


chat = MinioDeploy
