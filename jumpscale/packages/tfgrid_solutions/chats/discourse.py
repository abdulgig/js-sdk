import math

from jumpscale.loader import j
from jumpscale.sals.chatflows.chatflows import GedisChatBot, chatflow_step, StopChatFlow
import uuid
from jumpscale.sals.reservation_chatflow import deployer
import nacl
import nacl.signing
import random


class Discourse(GedisChatBot):
    FLIST_URL = "https://hub.grid.tf/omar0.3bot/omarelawady-discourse_all_in_one-latest.flist"

    steps = [
        "discourse_start",
        "discourse_name",
        "discourse_smtp_info",
        "discourse_email",
        "select_pool",
        "discourse_network",
        "overview",
        "reservation",
        "discourse_access",
    ]

    title = "Discourse"

    @chatflow_step()
    def discourse_start(self):
        self.solution_id = uuid.uuid4().hex
        self.query = dict()
        self.md_show("# This wizard will help you deploy discourse", md=True)
        self.threebot_name = j.data.text.removesuffix(self.user_info()["username"], ".3bot")
        self.solution_metadata = {}

    @chatflow_step(title="Solution name")
    def discourse_name(self):
        self.solution_name = deployer.ask_name(self)

    @chatflow_step(title="SMTP information")
    def discourse_smtp_info(self):
        form = self.new_form()
        self.smtp_server = form.string_ask("SMTP server address", required=True)
        self.smtp_username = form.string_ask("SMTP server username", required=True)
        self.smtp_password = form.secret_ask("SMTP server password", required=True)
        form.ask()
        self.smtp_server = self.smtp_server.value
        self.smtp_username = self.smtp_username.value
        self.smtp_password = self.smtp_password.value

    @chatflow_step(title="Email")
    def discourse_email(self):
        self.email = deployer.ask_email(self)
        self.container_resources()

    def container_resources(self):
        self.resources = dict()
        self.resources["cpu"] = 1
        self.resources["memory"] = 2048
        self.resources["disk_size"] = 4096
        self.resources["default_disk_type"] = "SSD"
        self.query = {
            "cru": self.resources["cpu"],
            "mru": math.ceil(self.resources["memory"] / 1024),
            "sru": math.ceil(self.resources["disk_size"] / 1024),
        }

    @chatflow_step(title="Pool")
    def select_pool(self):
        cu, su = deployer.calculate_capacity_units(**self.query)
        self.pool_id = deployer.select_pool(self, cu=cu, su=su)

    @chatflow_step(title="Network")
    def discourse_network(self):
        self.network_view = deployer.select_network(self)
        self.container_node_id()
        self.container_ip()
        self.select_domain()

    def container_node_id(self):
        self.selected_node = deployer.schedule_container(self.pool_id, **self.query)

    def container_ip(self):
        self.network_view_copy = self.network_view.copy()
        result = deployer.add_network_node(
            self.network_view.name, self.selected_node, self.pool_id, self.network_view_copy, bot=self
        )
        if result:
            self.md_show_update("Deploying Network on Nodes....")
            for wid in result["ids"]:
                success = deployer.wait_workload(wid)
                if not success:
                    raise StopChatFlow(f"Failed to add node {self.selected_node.node_id} to network {wid}")
            self.network_view_copy = self.network_view_copy.copy()
        self.ip_address = self.network_view_copy.get_free_ip(self.selected_node)

    def select_domain(self):
        gateways = deployer.list_all_gateways()
        if not gateways:
            raise StopChatFlow("There are no available gateways in the farms bound to your pools.")

        domains = dict()
        for gw_dict in gateways.values():
            gateway = gw_dict["gateway"]
            for domain in gateway.managed_domains:
                domains[domain] = gw_dict

        self.domain = random.choice(list(domains.keys()))

        self.gateway = domains[self.domain]["gateway"]
        self.gateway_pool = domains[self.domain]["pool"]
        self.domain = f"{self.threebot_name}-{self.solution_name}.{self.domain}"

        self.addresses = []
        for ns in self.gateway.dns_nameserver:
            self.addresses.append(j.sals.nettools.get_host_by_name(ns))

        self.secret = f"{j.core.identity.me.tid}:{uuid.uuid4().hex}"

    @chatflow_step(title="Confirmation")
    def overview(self):
        self.metadata = {
            "Solution Name": self.solution_name,
            "Network": self.network_view.name,
            "Node ID": self.selected_node.node_id,
            "Pool": self.pool_id,
            "CPU": self.resources["cpu"],
            "Memory": self.resources["memory"],
            "Disk Size": self.resources["disk_size"],
            "IP Address": self.ip_address,
        }
        self.md_show_confirm(self.metadata)

    @chatflow_step(title="Reservation")
    def reservation(self):
        metadata = {
            "name": self.solution_name,
            "form_info": {"chatflow": "discourse", "Solution name": self.solution_name},
        }
        threebot_private_key = nacl.signing.SigningKey.generate().encode(nacl.encoding.Base64Encoder).decode("utf-8")

        env = {
            "pub_key": "",
            "DISCOURSE_VERSION": "staging",
            "RAILS_ENV": "production",
            "DISCOURSE_HOSTNAME": self.domain,
            "DISCOURSE_SMTP_USER_NAME": self.smtp_username,
            "DISCOURSE_SMTP_ADDRESS": self.smtp_server,
            "DISCOURSE_DEVELOPER_EMAILS": self.email,
            "DISCOURSE_SMTP_PORT": "587",
            "THREEBOT_URL": "https://login.threefold.me",
            "OPEN_KYC_URL": "https://openkyc.live/verification/verify-sei",
        }

        secret_env = {
            "THREEBOT_PRIVATE_KEY": threebot_private_key,
            "FLASK_SECRET_KEY": str(uuid.uuid4()),
            "DISCOURSE_SMTP_PASSWORD": self.smtp_password,
        }

        # reserve subdomain
        _id = deployer.create_subdomain(
            pool_id=self.gateway_pool.pool_id,
            gateway_id=self.gateway.node_id,
            subdomain=self.domain,
            addresses=self.addresses,
            solution_uuid=self.solution_id,
            **metadata,
        )

        success = deployer.wait_workload(_id, self)
        if not success:
            raise StopChatFlow(f"Failed to create subdomain {self.domain} on gateway" f" {self.gateway.node_id} {_id}")

        # expose threebot container
        _id = deployer.expose_address(
            pool_id=self.pool_id,
            gateway_id=self.gateway.node_id,
            network_name=self.network_view.name,
            local_ip=self.ip_address,
            port=80,
            tls_port=443,
            trc_secret=self.secret,
            node_id=self.selected_node.node_id,
            reserve_proxy=True,
            domain_name=self.domain,
            proxy_pool_id=self.gateway_pool.pool_id,
            solution_uuid=self.solution_id,
            **metadata,
        )
        success = deployer.wait_workload(_id, self)
        if not success:
            # solutions.cancel_solution(self.workload_ids)
            raise StopChatFlow(f"Failed to create trc container on node {self.selected_node.node_id}" f" {_id}")

        entrypoint = f"/.start_discourse.sh"
        self.entrypoint = entrypoint
        # reserve container
        self.resv_id = deployer.deploy_container(
            pool_id=self.pool_id,
            node_id=self.selected_node.node_id,
            network_name=self.network_view.name,
            ip_address=self.ip_address,
            flist=self.FLIST_URL,
            cpu=self.resources["cpu"],
            memory=self.resources["memory"],
            disk_size=self.resources["disk_size"],
            entrypoint=entrypoint,
            env=env,
            secret_env=secret_env,
            interactive=False,
            **metadata,
            solution_uuid=self.solution_id,
        )
        success = deployer.wait_workload(self.resv_id, self)
        if not success:
            raise StopChatFlow(f"Failed to deploy workload {self.resv_id}")

    @chatflow_step(title="Success", disable_previous=True)
    def discourse_access(self):
        res = f"""\
# Discourse has been deployed successfully: your reservation id is: {self.resv_id}
The site is deployed on {self.domain}.
 It takes approximately 10 minutes to deploy.
                """
        self.md_show(res, md=True)


chat = Discourse