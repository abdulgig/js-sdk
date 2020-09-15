from jumpscale.packages.marketplace.chats.publisher import Publisher
from jumpscale.sals.chatflows.chatflows import chatflow_step


class WebsiteDeploy(Publisher):

    title = "Deploy a Website"
    SOLUTION_TYPE = "website"  # chatflow used to deploy the solution

    @chatflow_step(title="Website Setup")
    def configuration(self):
        self.user_email = self.user_info()["email"]
        form = self.new_form()
        title = form.string_ask("Title", required=True)
        url = form.string_ask("Repository URL", required=True, is_git_url=True)
        branch = form.string_ask("Branch", required=True)
        form.ask(Publisher.MD_CONFIG_MSG, md=True)
        self.envars = {
            "TYPE": "www",
            "NAME": "entrypoint",
            "TITLE": title.value,
            "URL": url.value,
            "BRANCH": branch.value,
            "EMAIL": self.user_email,
        }


chat = WebsiteDeploy
