#!/usr/bin/env python3

import sys
import yaml
import os
import json


from kubernetes import config
from kubernetes.client import ApiClient
from boto3 import Session

from openshift.dynamic import DynamicClient

APISCHEME_SSS_NAME = "cloud-ingress-operator"

# from kubeconfig
# k8s_client = config.new_client_from_config()
# dyn_client = DynamicClient(k8s_client)

# in cluster client creation
k8s_client = ApiClient(config.load_incluster_config())
dyn_client = DynamicClient(k8s_client)


def get_sss():
    selectorsyncsets = dyn_client.resources.get(
        api_version="hive.openshift.io/v1", kind="SelectorSyncSet"
    )
    return selectorsyncsets.get(name=APISCHEME_SSS_NAME)


def get_hive_ips():
    nodes = dyn_client.resources.get(api_version="v1", kind="Node")

    hive_ips = []
    for node in nodes.get().items:
        for a in node.status.addresses:
            if a.type == "ExternalIP":
                # add /32 to the end since they're not recorded as CIDR blocks
                hive_ips.append("{}/32".format(a.address))

    print("found %d hive IPs" % len(hive_ips))

    return hive_ips


def get_bastion_ips(resource):
    bastion_ips = resource.metadata.annotations.allowedCIDRBlocks or ""

    bastion_ips = json.loads(bastion_ips)
    print("found %d bastion IPs" % len(bastion_ips))

    return bastion_ips


def _manage_ips(ips, operation):
    """ Submit a request to add/remove entries to/from app-interface """
    allowed_operations = ['add', 'remove']
    if operation not in allowed_operations:
        print("operation is not allowed")
        sys.exit(1)

    aws_access_key_id = os.environ['aws_access_key_id']
    aws_secret_access_key = os.environ['aws_secret_access_key']
    aws_region = os.environ['aws_region']
    queue_url = os.environ['queue_url']

    session = Session(
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        region_name=aws_region,
    )
    sqs = session.client('sqs')
    body = {
        'pr_type': 'create_cloud_ingress_operator_cidr_blocks_mr',
        'cidr_blocks': list(ips),
        'operation': operation
    }
    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(body)
    )


def add_ips(ips):
    """ Submit a request to add entries to app-interface """
    _manage_ips(ips, operation='add')


def remove_ips(ips):
    """ Submit a request to remove entries from app-interface """
    _manage_ips(ips, operation='remove')


sss = get_sss()

for resource in sss.spec.resources:
    if resource.kind == "APIScheme" and resource.metadata.name == "rh-api":
        break
else:
    print("Couldn't find the rh-api APIScheme!")
    sys.exit(1)

hive_ips = get_hive_ips()
if not hive_ips:
    print("Couldn't find any hive IPs! Assuming this means we're running "
          "on v4, and not that there's an actual problem. Bailing with "
          "'success' status.")
    sys.exit(0)

all_ips = set(hive_ips + get_bastion_ips(resource))
if not all_ips:
    print("Not enough IPs!")
    sys.exit(1)

ingress = resource.spec.managementAPIServerIngress

ingress_ips = set(ingress.allowedCIDRBlocks)
missing_ips = all_ips - ingress_ips
if not missing_ips:
    print("No IPs to add, no-op")
    sys.exit(0)

add_ips(missing_ips)
