from typing import List, Iterator

from nacl.encoding import Base64Encoder

from jumpscale.core import identity
from jumpscale.core.exceptions import Input, NotFound

from .auth import HTTPSignatureAuth
from .base import BaseResource
from .models import Farm
from .pagination import get_all, get_page


def _build_query(threebot_id: int = None, name: str = None,) -> dict:
    query = {}
    if threebot_id:
        query["owner"] = threebot_id
    if name:
        query["name"] = name
    return query


class Farms(BaseResource):
    _resource = "farms"

    def list(self, threebot_id: int = None, name: str = None, page: int = None) -> List[Farm]:
        """
        list farms

        :param threebot_id: if specified, filter the farm by its owner threebot ID
        :type threebot_id: int, optional
        :param name: if specified, filter the farm by name
        :type name: str, optional
        :return: list(Farm)
        :rtype: list
        """
        query = _build_query(threebot_id=threebot_id, name=name)
        if page:
            farms, _ = get_page(self._session, page, Farm, self._url, query)
        else:
            farms = list(self.iter(threebot_id, name))

        return farms

    def iter(self, threebot_id: int = None, name: str = None) -> Iterator[Farm]:
        """
        return an iterator that yield all the farm from the explorer

        :param threebot_id: if specified, filter the farm by its owner threebot ID
        :type threebot_id: int, optional
        :param name: if specified, filter the farm by name
        :type name: str, optional
        :yield: farm object
        :rtype: Iterator[Farm]
        """
        query = _build_query(threebot_id=threebot_id, name=name)
        yield from get_all(self._session, Farm, self._url, query)

    def new(self) -> Farm:
        """
        new creates a new empty Farm object

        :return: farm object
        :rtype: Farm
        """
        return Farm()

    def register(self, farm: Farm) -> int:
        """
        register a farm on the explorer

        :param farm: farm object created by the "new" method
        :type farm: Farm
        :return: the created farm ID
        :rtype: integer
        """
        resp = self._session.post(self._url, json=farm.to_dict())
        return resp.json()["id"]

    def get(self, farm_id: int = None, farm_name: str = None) -> Farm:
        """
        get the detail of a farm

        :param farm_id: ID of the farm to retrieve
        :type farm_id: int, optional
        :param farm_name: Name of the farm to retrieve, if you use name the client loops over all farm and match on the name.
                          Be carefull using this in performance critical code path
        :type farm_name: str, optional
        :raises NotFound: if no farm with the specified ID or name if found
        :raises Input: if farm_id and farm_name are None
        :return: Farm object
        :rtype: Farm
        """
        if farm_name:
            for farm in self.iter():
                if farm.name == farm_name:
                    return farm
            else:
                raise NotFound(f"Could not find farm with name {farm_name}")
        elif not farm_id:
            raise Input("farms.get requires at least farm_id or farm_name")

        resp = self._session.get(f"{self._url}/{farm_id}")
        return Farm.from_dict(resp.json())

    def update(self, farm: Farm) -> bool:
        """
        update an existing farm details.
        the farm updated is the one identified by farm.id

        :param farm: farm object
        :type farm: Farm
        :return: true when update is successfull
        :rtype: bool
        """
        self._session.put(f"{self._url}/{farm.id}", json=farm.to_dict())
        return True

    def delete(self, farm_id, node_id):
        self._session.delete(f"{self._url}/{farm_id}/{node_id}")
        return True
