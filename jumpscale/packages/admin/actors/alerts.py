from jumpscale.servers.gedis.baseactor import BaseActor, actor_method
from jumpscale.god import j


class Alerts(BaseActor):
    @actor_method
    def list_alerts(self) -> str:
        """
            get all alerts
        """
        ret = [alert.json for alert in j.tools.alerthandler.find()]
        return j.data.serializers.json.dumps({"data": ret})

    @actor_method
    def get_alerts_count(self) -> str:
        """
            get count of alerts
        """
        return j.data.serializers.json.dumps({"data": j.tools.alerthandler.count()})

    @actor_method
    def delete_alerts(self, ids: list = [], identifiers: list = []):
        """
            delete list of alerts
        """
        try:
            if ids:
                for _id in ids:
                    j.tools.alerthandler.delete(_id)
            elif identifiers:
                for _identifier in identifiers:
                    j.tools.alerthandler.delete(_identifier)
        except Exception as e:
            raise e

    @actor_method
    def delete_all_alerts(self):
        """
            delete all alerts
        """
        try:
            j.tools.alerthandler.delete_all()
        except Exception as e:
            raise e


Actor = Alerts