from jumpscale.loader import j
from jumpscale.clients.explorer.models import TfgridWorkloadsPoolCreated1


class Pools:
    def __init__(self, explorer):
        self._model_create = TfgridWorkloadsPoolCreated1
        self._pools = explorer.pools
        self._farms = explorer.farms
        self._nodes = explorer.nodes
        self._gateways = explorer.gateway

    def _reserve(self, pool, identity=None):
        me = identity if identity else j.core.identity.me
        pool.customer_tid = me.tid
        pool.json = j.data.serializers.json.dumps(pool.data_reservation.to_dict())
        pool.customer_signature = me.nacl.sign_hex(pool.json.encode()).decode()
        return self._pools.create(pool)

    def create(self, cu, su, farm, currencies=None, identity=None):
        if not currencies:
            currencies = ["TFT"]

        farm_id = farm
        if isinstance(farm, str):
            farm = self._farms.get(farm_name=farm)
            farm_id = farm.id

        node_ids = []
        for node in self._nodes.iter(farm_id=farm_id):
            node_ids.append(node.node_id)
        for gw in self._gateways.iter(farm_id=farm_id):
            node_ids.append(gw.node_id)

        pool = self._pools.new()
        pool.data_reservation.pool_id = 0
        pool.data_reservation.cus = cu
        pool.data_reservation.sus = su
        pool.data_reservation.node_ids = node_ids
        pool.data_reservation.currencies = currencies
        return self._reserve(pool, identity=identity)

    def extend(self, pool_id, cu, su, currencies=None):
        p = self.get(pool_id)
        if not currencies:
            currencies = ["TFT"]

        pool = self._pools.new()
        pool.data_reservation.pool_id = p.pool_id
        pool.data_reservation.cus = int(p.cus + cu)
        pool.data_reservation.sus = int(p.sus + su)
        pool.data_reservation.node_ids = p.node_ids
        pool.data_reservation.currencies = currencies
        return self._reserve(pool)

    def get(self, pool_id):
        return self._pools.get(pool_id)

    def iter(self):
        return self._pools.iter()

    def list(self, page=None):
        return self._pools.list()