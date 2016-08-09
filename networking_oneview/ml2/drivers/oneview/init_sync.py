#
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

from neutron._i18n import _LW
from neutron.plugins.ml2.drivers.oneview import database_manager as db_manager
from neutron.plugins.ml2.drivers.oneview import neutron_oneview_client
from neutron.plugins.ml2.drivers.oneview import common
from oslo_config import cfg
from oslo_log import log
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import requests
requests.packages.urllib3.disable_warnings()

CONF = cfg.CONF
LOG = log.getLogger(__name__)


class InitSync(object):
    def __init__(self, oneview_client, connection):
        self.client = neutron_oneview_client.Client(
            oneview_client
        )
        Session = sessionmaker(bind=create_engine(connection))
        self.session = Session()

        self.uplinkset_mappings_dict = (
            common.load_conf_option_to_dict(CONF.oneview.uplinkset_mapping)
        )
        self.oneview_network_mapping_dict = (
            common.load_oneview_network_mapping_conf_to_dict(
                CONF.oneview.flat_net_mappings
            )
        )

    def check_and_sync_mapped_uplinksets_on_db(self):
        for neutron_network, segment in (
            db_manager.list_networks_and_segments_with_physnet(self.session)
        ):
            physical_network_uplinkset_list = self.uplinkset_mappings_dict.get(
                segment.physical_network
            )
            if physical_network_uplinkset_list is None:
                continue

            neutron_oneview_network = db_manager.get_neutron_oneview_network(
                self.session, neutron_network.id
            )

            oneview_network_id = neutron_oneview_network.oneview_network_uuid

            oneview_network_uplink_list = db_manager.get_network_uplinksets(
                self.session, oneview_network_id
            )

            network_uplinkset_list = [
                network_uplinkset.oneview_uplinkset_uuid
                for network_uplinkset in oneview_network_uplink_list
            ]

            if neutron_oneview_network is None:
                pass
            else:
                for uplinkset_id in network_uplinkset_list:
                    if uplinkset_id not in physical_network_uplinkset_list:
                        self.client.uplinkset.remove_network(
                            self.session, uplinkset_id, oneview_network_id
                        )

                for uplinkset_id in physical_network_uplinkset_list:
                    if uplinkset_id not in network_uplinkset_list:
                        self.client.uplinkset.add_network(
                            self.session, uplinkset_id, oneview_network_id
                        )

    def check_mapped_networks_on_db_and_create_on_oneview(self):
        for neutron_network in db_manager.list_neutron_networks(
            self.session
        ):
            segment = db_manager.get_network_segment(
                self.session, neutron_network.id
            )

            if db_manager.get_neutron_oneview_network(
                self.session, neutron_network.id
            ) is not None or segment.physical_network is None:
                continue

            neutron_network_dict = {
                'id': neutron_network.id,
                'name': neutron_network.name,
                'provider:segmentation_id': segment.segmentation_id,
                'provider:physical_network': segment.physical_network,
                'provider:network_type': segment.network_type
            }

            uplinkset_id_list = (
                self.client.uplinkset.get_uplinkset_by_type(
                    self.uplinkset_mappings_dict.get(
                        segment.physical_network
                    ),
                    segment.network_type
                )
            )

            if len(uplinkset_id_list) > 0:
                self.client.network.create(
                    self.session, neutron_network_dict, uplinkset_id_list,
                    self.oneview_network_mapping_dict
                )
            else:
                LOG.warning(_LW(
                    "Physical Network %(physnet)s has no a valid Uplink "
                    "Set associated for type %(type)s" % {
                        'physnet': segment.physical_network,
                        'type': segment.network_type
                    }
                ))
