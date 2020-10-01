from jumpscale.packages.marketplace.chats.blog import BlogDeploy
from utils.gedispatch import GedisChatBotPatch


class BlogAutomated(GedisChatBotPatch, BlogDeploy):
    QS = {"Title": "title", "Repository URL": "repo", "Branch": "branch"}
