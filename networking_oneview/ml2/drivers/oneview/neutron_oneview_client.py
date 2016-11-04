# Copyright 2016 Hewlett Packard Development Company, LP
# Copyright 2016 Universidade Federal de Campina Grande
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import abc
import six

from hpOneView import exceptions
from neutron.plugins.ml2.drivers.oneview import common
from neutron.plugins.ml2.drivers.oneview import database_manager as db_manager
from oslo_log import log

LOG = log.getLogger(__name__)

MAPPING_TYPE_NONE = 0
FLAT_PHYSNET_NET_MAPPING_TYPE = 1
PHYSNET_UPLINKSET_MAPPING_TYPE = 2

NETWORK_TYPE_TAGGED = 'tagged'
NETWORK_TYPE_UNTAGGED = 'untagged'


def validate_local_link_information(local_link_information_list):
    if not local_link_information_list:
        return False

    if len(local_link_information_list) > 1:
        LOG.warning("'local_link_information' must have only one value")
        return False

    local_link_information = local_link_information_list[0]
    switch_info = local_link_information.get('switch_info')

    if switch_info is None:
        LOG.warning(
            "Port 'local_link_information' must contain 'switch_info'."
        )
        return False

    server_hardware_id = switch_info.get('server_hardware_id')
    bootable = switch_info.get('bootable')

    if server_hardware_id is None or bootable is None:
        LOG.warning(
            "Port 'local_link_information' must contain 'server_hardware_id'"
            " and 'bootable'."
        )
        return False

    if type(bootable) != bool:
        LOG.warning(
            "Port 'local_link_information' must 'bootable' must be a boolean."
        )
        return False

    return True


def is_port_valid_to_reflect_on_oneview(
    vnic_type, neutron_oneview_network, local_link_information_list
):
    if vnic_type != 'baremetal':
        return False
    if neutron_oneview_network is None:
        return False
    return validate_local_link_information(local_link_information_list)


@six.add_metaclass(abc.ABCMeta)
class ResourceManager:
    def __init__(
        self, oneview_client, physnet_uplinkset_mapping,
        flat_physnet_net_mapping
    ):
        self.oneview_client = oneview_client
        self.physnet_uplinkset_mapping = physnet_uplinkset_mapping
        self.flat_physnet_net_mapping = flat_physnet_net_mapping


class Network(ResourceManager):
    _NEUTRON_NET_TYPE_TO_ONEVIEW_NET_TYPE = {
        'vxlan': 'tagged',
        'vlan': 'tagged',
        'flat': 'untagged',
    }

    def _is_physnet_in_uplinkset_mapping(self, physical_network):
        if self.physnet_uplinkset_mapping.get(NETWORK_TYPE_TAGGED).get(
            physical_network
        ):
            return True
        return self.physnet_uplinkset_mapping.get(NETWORK_TYPE_UNTAGGED).get(
            physical_network
        )

    def _is_physical_network_managed(self, physical_network):
        return (
            self._is_physnet_in_uplinkset_mapping(physical_network) or
            self.flat_physnet_net_mapping.get(physical_network)
        )

    def _create_network_on_oneview(self, name, network_type, seg_id):
        options = {
            'name': name,
            'ethernetNetworkType': network_type,
            'vlanId': seg_id,
            'purpose': 'General',
            'smartLink': False,
            'privateNetwork': False,
        }
        return self.oneview_client.ethernet_networks.create(options)

    def _add_network_to_uplink_sets(self, network_id, uplinksets_id_list):
        for uplinkset_id in uplinksets_id_list:
            try:
                self.oneview_client.uplink_sets.add_ethernet_networks(
                    uplinkset_id, network_id
                )
            except exceptions.HPOneViewException as err:
                LOG.error(
                    "Driver could not add network %(network_id)s to uplink set"
                    " %(uplink_set_id)s. %(error)s" % {
                        'network_id': network_id,
                        'uplink_set_id': uplinkset_id,
                        'error': err
                    }
                )

    def _get_network_mapping_type(self, physical_network, network_type):
        physnet_in_uplinkset_mapping = self._is_physnet_in_uplinkset_mapping(
            physical_network
        )
        if network_type == 'vlan' and physnet_in_uplinkset_mapping:
            return PHYSNET_UPLINKSET_MAPPING_TYPE
        elif physical_network in self.flat_physnet_net_mapping:
            return FLAT_PHYSNET_NET_MAPPING_TYPE
        elif physnet_in_uplinkset_mapping:
            return PHYSNET_UPLINKSET_MAPPING_TYPE

        return MAPPING_TYPE_NONE

    def create(self, session, network_dict):
        network_id = network_dict.get('id')
        network_seg_id = network_dict.get('provider:segmentation_id')
        physical_network = network_dict.get('provider:physical_network')
        network_type = network_dict.get('provider:network_type')

        if not self._is_physical_network_managed(physical_network):
            return

        mapping_type = self._get_network_mapping_type(
            physical_network, network_type
        )

        if mapping_type is MAPPING_TYPE_NONE:
            return

        uplinksets_id_list = []
        if mapping_type == PHYSNET_UPLINKSET_MAPPING_TYPE:
            uplinksets_id_list = self.physnet_uplinkset_mapping.get(
                self._NEUTRON_NET_TYPE_TO_ONEVIEW_NET_TYPE.get(network_type)
            ).get(physical_network)
            network_type = 'Tagged' if network_seg_id else 'Untagged'
            oneview_network = self._create_network_on_oneview(
                name="Neutron[" + network_id + "]",
                network_type=network_type, seg_id=network_seg_id
            )
            oneview_network_id = common.id_from_uri(oneview_network.get('uri'))
            self._add_network_to_uplink_sets(
                oneview_network_id, uplinksets_id_list
            )
        elif mapping_type == FLAT_PHYSNET_NET_MAPPING_TYPE:
            oneview_network_id = self.flat_physnet_net_mapping.get(
                physical_network
            )

        db_manager.map_neutron_network_to_oneview(
            session, network_id, oneview_network_id, uplinksets_id_list,
            mapping_type == PHYSNET_UPLINKSET_MAPPING_TYPE
        )

    def delete(self, session, network_dict):
        network_id = network_dict.get('id')
        physical_network = network_dict.get('provider:physical_network')

        neutron_oneview_network = db_manager.get_neutron_oneview_network(
            session, network_id
        )
        if neutron_oneview_network is None:
            return

        oneview_network_id = neutron_oneview_network.oneview_network_id
        if neutron_oneview_network.manageable:
            self.oneview_client.ethernet_networks.delete(oneview_network_id)

        db_manager.delete_neutron_oneview_network(session, network_id)
        db_manager.delete_oneview_network_uplinkset_by_network(
            session, oneview_network_id
        )


class Port(ResourceManager):
    def _server_profile_from_server_hardware(self, server_hardware_id):
        server_hardware = self.oneview_client.server_hardware.get(
            server_hardware_id
        )
        server_profile_uri = server_hardware.get('serverProfileUri')
        return self.oneview_client.server_profiles.get(server_profile_uri)

    def _get_boot_priority(self, server_profile, bootable):
        def is_boot_priority_available(connections, boot_priority):
            for connection in connections:
                if connection.get('boot').get('priority') == boot_priority:
                    return False
            return True

        if bootable:
            connections = server_profile.get('connections')
            if is_boot_priority_available(connections, 'Primary'):
                return 'Primary'
            elif is_boot_priority_available(connections, 'Secondary'):
                return 'Secondary'
        return 'NotBootable'

    def _port_id_from_mac(self, server_hardware_id, mac_address):
        def get_port_info(server_hardware_id, mac_address):
            server_hardware = self.oneview_client.server_hardware.get(
                server_hardware_id
            )
            port_map = server_hardware.get('portMap')
            device_slots = port_map.get('deviceSlots')

            for device_slot in device_slots:
                physical_ports = device_slot.get('physicalPorts')
                for physical_port in physical_ports:
                    virtual_ports = physical_port.get('virtualPorts')
                    for virtual_port in virtual_ports:
                        mac = virtual_port.get('mac')
                        if mac == mac_address.upper():
                            return {
                                'virtual_port_function': virtual_port.get(
                                    'portFunction'
                                ),
                                'physical_port_number': physical_port.get(
                                    'portNumber'
                                ),
                                'device_slot_port_number': device_slot.get(
                                    'slotNumber'
                                ),
                                'device_slot_location': device_slot.get(
                                    'location'
                                ),
                            }

        port_info = get_port_info(server_hardware_id, mac_address)

        return str(port_info.get('device_slot_location')) + " " +\
            str(port_info.get('device_slot_port_number')) + ":" +\
            str(port_info.get('physical_port_number')) + "-" +\
            str(port_info.get('virtual_port_function'))

    def create(self, session, port_dict):
        vnic_type = port_dict.get('binding:vnic_type')
        network_id = port_dict.get('network_id')
        mac_address = port_dict.get('mac_address')

        neutron_oneview_network = db_manager.get_neutron_oneview_network(
            session, network_id
        )
        local_link_information_list = common.local_link_information_from_port(
            port_dict
        )

        if not is_port_valid_to_reflect_on_oneview(
            vnic_type, neutron_oneview_network, local_link_information_list
        ):
            LOG.info("Port not valid to reflect on OneView.")
            return

        switch_info = local_link_information_list[0].get('switch_info')
        server_hardware_id = switch_info.get('server_hardware_id')
        bootable = switch_info.get('bootable')

        network_uri = common.network_uri_from_id(
            neutron_oneview_network.oneview_network_id
        )
        server_profile = self._server_profile_from_server_hardware(
            server_hardware_id
        )
        boot_priority = self._get_boot_priority(server_profile, bootable)
        port_id = self._port_id_from_mac(server_hardware_id, mac_address)

        server_profile['connections'].append({
            'portId': port_id,
            'networkUri': network_uri,
            'boot': {'priority': boot_priority},
            'functionType': 'Ethernet'
        })

        self.oneview_client.server_profiles.update(
            resource=server_profile,
            id_or_uri=server_profile.get('uri')
        )

    def delete(self, session, port_dict):
        def connection_with_mac_address(connections, mac_address):
            for connection in connections:
                if connection.get('mac') == mac_address:
                    return connection

        vnic_type = port_dict.get('binding:vnic_type')
        network_id = port_dict.get('network_id')
        mac_address = port_dict.get('mac_address')

        neutron_oneview_network = db_manager.get_neutron_oneview_network(
            session, network_id
        )
        local_link_information_list = common.local_link_information_from_port(
            port_dict
        )

        if not is_port_valid_to_reflect_on_oneview(
            vnic_type, neutron_oneview_network, local_link_information_list
        ):
            return

        switch_info = local_link_information_list[0].get('switch_info')
        server_hardware_id = switch_info.get('server_hardware_id')

        network_uri = common.network_uri_from_id(
            neutron_oneview_network.oneview_network_id
        )
        server_profile = self._server_profile_from_server_hardware(
            server_hardware_id
        )
        connection = connection_with_mac_address(
            server_profile.get('connections'), mac_address
        )

        if connection:
            server_profile.get('connections').remove(connection)

        self.oneview_client.server_profiles.update(
            resource=server_profile,
            id_or_uri=server_profile.get('uri')
        )


class Client:
    def __init__(
        self, oneview_client, physnet_uplinkset_mapping,
        flat_physnet_net_mapping
    ):
        physnet_uplinkset_mapping = self._physnet_uplinkset_mapping_by_type(
            oneview_client, physnet_uplinkset_mapping
        )
        self.network = Network(
            oneview_client, physnet_uplinkset_mapping, flat_physnet_net_mapping
        )
        self.port = Port(
            oneview_client, physnet_uplinkset_mapping, flat_physnet_net_mapping
        )

    def _physnet_uplinkset_mapping_by_type(
        self, oneview_client, physnet_uplinkset_mapping
    ):
        physnet_uplinkset_mapping_by_type = {}

        physnet_uplinkset_mapping_by_type[NETWORK_TYPE_TAGGED] = (
            self._get_uplinkset_by_type(
                oneview_client, physnet_uplinkset_mapping, NETWORK_TYPE_TAGGED
            )
        )

        physnet_uplinkset_mapping_by_type[NETWORK_TYPE_UNTAGGED] = (
            self._get_uplinkset_by_type(
                oneview_client, physnet_uplinkset_mapping,
                NETWORK_TYPE_UNTAGGED
            )
        )

        return physnet_uplinkset_mapping_by_type

    def _get_uplinkset_by_type(
        self, oneview_client, physnet_uplinkset_mapping, net_type
    ):
        def try_to_get_uplinkset(oneview_client, uplinkset_id):
            try:
                return oneview_client.uplink_sets.get(uplinkset_id)
            except exceptions.HPOneViewException as err:
                LOG.error(err)

        uplinksets_by_type = {}
        for physnet in physnet_uplinkset_mapping:
            for uplinkset_id in physnet_uplinkset_mapping.get(physnet):
                uplinkset = try_to_get_uplinkset(oneview_client, uplinkset_id)
                if (
                    uplinkset is None or
                    uplinkset.get('ethernetNetworkType').lower() == net_type
                ):
                    if uplinksets_by_type.get(physnet) is None:
                        uplinksets_by_type[physnet] = []
                    uplinksets_by_type[physnet].append(uplinkset_id)

        return uplinksets_by_type
