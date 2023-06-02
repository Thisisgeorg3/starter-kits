import logging
import sys
import threading
from datetime import datetime, timedelta

import forta_agent
import pandas as pd
import time
import os
import requests
from forta_agent import get_json_rpc_url
from hexbytes import HexBytes
from web3 import Web3
from forta_agent import FindingSeverity
import traceback
import json

from src.findings import AlertCombinerFinding
from src.constants import (ENTITY_CLUSTERS_MAX_QUEUE_SIZE, FP_CLUSTERS_QUEUE_MAX_SIZE, BASE_BOTS, ENTITY_CLUSTER_BOT_ALERT_ID, ALERTED_CLUSTERS_MAX_QUEUE_SIZE,
                           FP_MITIGATION_BOTS, ALERTS_LOOKBACK_WINDOW_IN_HOURS, ENTITY_CLUSTER_BOT, ANOMALY_SCORE_THRESHOLD_STRICT, ANOMALY_SCORE_THRESHOLD_LOOSE,
                           MIN_ALERTS_COUNT, ALERTS_DATA_KEY, ALERTED_CLUSTERS_STRICT_KEY, ALERTED_CLUSTERS_LOOSE_KEY, ENTITY_CLUSTERS_KEY, FP_MITIGATION_CLUSTERS_KEY,
                           CONTEXT_KEY, CONTEXT_QUEUE_MAX_SIZE, CONTEXT_BOTS, DEFAULT_ANOMALY_SCORE, HIGHLY_PRECISE_BOTS,
                           ALERTED_CLUSTERS_FP_MITIGATED_KEY, END_USER_ATTACK_BOTS, END_USER_ATTACK_CLUSTERS_KEY, END_USER_ATTACK_CLUSTERS_QUEUE_MAX_SIZE, POLYGON_VALIDATOR_ALERT_COUNT_THRESHOLD)
from src.L2Cache import L2Cache
from src.blockchain_indexer_service import BlockChainIndexer

web3 = Web3(Web3.HTTPProvider(get_json_rpc_url()))
block_chain_indexer = BlockChainIndexer()

CHAIN_ID = -1

FINDINGS_CACHE = []
CONTRACT_CACHE = dict()  # address -> is_contract
ENTITY_CLUSTERS = dict()  # address -> cluster
ALERTS = []
ALERT_DATA = dict()  # cluster -> pd.DataFrame
ALERTED_CLUSTERS_STRICT = []  # cluster
ALERTED_CLUSTERS_LOOSE = []  # cluster
ALERTED_CLUSTERS_FP_MITIGATED = []  # cluster
FP_MITIGATION_CLUSTERS = []  # cluster
END_USER_ATTACK_CLUSTERS = []  # cluster
CONTEXT = dict()  # transaction_hash, metadata
ALERT_ID_STAGE_MAPPING = dict()  # (bot_id, alert_id) -> stage

root = logging.getLogger()
root.setLevel(logging.DEBUG)

handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
root.addHandler(handler)

label_api = "https://api.forta.network/labels/state?sourceIds=etherscan,0x6f022d4a65f397dffd059e269e1c2b5004d822f905674dbf518d968f744c2ede&entities="

def initialize():
    """
    this function initializes the state variables that are tracked across tx and blocks
    it is called from test to reset state between tests
    """
    logging.debug('initializing')

    global CHAIN_ID
    try:
        CHAIN_ID = os.environ.get('FORTA_CHAIN_ID')
        if CHAIN_ID is None:
            CHAIN_ID = web3.eth.chain_id
        else:
            CHAIN_ID = int(CHAIN_ID)
        logging.info(f"Set chain id to {CHAIN_ID}")
    except Exception as e:
        logging.error(f"Error getting chain id: {e}")
        raise e


    global ALERT_ID_STAGE_MAPPING
    ALERT_ID_STAGE_MAPPING = dict([((bot_id, alert_id), stage) for bot_id, alert_id, stage in BASE_BOTS])


    global ALERTED_CLUSTERS_FP_MITIGATED
    alerted_clusters = load(CHAIN_ID, ALERTED_CLUSTERS_FP_MITIGATED_KEY)
    ALERTED_CLUSTERS_FP_MITIGATED = [] if alerted_clusters is None else list(alerted_clusters)

    global ALERTED_CLUSTERS_STRICT
    alerted_clusters = load(CHAIN_ID, ALERTED_CLUSTERS_STRICT_KEY)
    ALERTED_CLUSTERS_STRICT = [] if alerted_clusters is None else list(alerted_clusters)

    global ALERTED_CLUSTERS_LOOSE
    alerted_clusters = load(CHAIN_ID, ALERTED_CLUSTERS_LOOSE_KEY)
    ALERTED_CLUSTERS_LOOSE = [] if alerted_clusters is None else list(alerted_clusters)

    global ALERT_DATA
    alerts = load(CHAIN_ID, ALERTS_DATA_KEY)
    ALERT_DATA = {} if alerts is None else dict(alerts)

    global ENTITY_CLUSTERS
    entity_cluster_alerts = load(CHAIN_ID, ENTITY_CLUSTERS_KEY)
    ENTITY_CLUSTERS = {} if entity_cluster_alerts is None else dict(entity_cluster_alerts)

    global CONTEXT
    context = load(CHAIN_ID, CONTEXT_KEY)
    CONTEXT = {} if context is None else dict(context)

    global FP_MITIGATION_CLUSTERS
    fp_mitigation_alerts = load(CHAIN_ID, FP_MITIGATION_CLUSTERS_KEY)
    FP_MITIGATION_CLUSTERS = [] if fp_mitigation_alerts is None else list(fp_mitigation_alerts)

    global END_USER_ATTACK_CLUSTERS
    end_user_attack_alerts = load(CHAIN_ID, END_USER_ATTACK_CLUSTERS_KEY)
    END_USER_ATTACK_CLUSTERS = [] if end_user_attack_alerts is None else list(end_user_attack_alerts)

    global FINDINGS_CACHE
    FINDINGS_CACHE = []

    global CONTRACT_CACHE
    CONTRACT_CACHE = {}

    subscription_json = []
    for bot, alertId, stage in BASE_BOTS:
        subscription_json.append({"botId": bot, "alertId": alertId, "chainId": CHAIN_ID})
        if CHAIN_ID in [10, 42161]:
            subscription_json.append({"botId": bot, "alertId": alertId, "chainId": 1})
   
    for bot, alertId in FP_MITIGATION_BOTS:
        subscription_json.append({"botId": bot, "alertId": alertId, "chainId": CHAIN_ID})
        if CHAIN_ID in [10, 42161]:
            subscription_json.append({"botId": bot, "alertId": alertId, "chainId": 1})

    for bot in END_USER_ATTACK_BOTS:
        subscription_json.append({"botId": bot, "chainId": CHAIN_ID})
        if CHAIN_ID in [10, 42161]:
            subscription_json.append({"botId": bot, "chainId": 1})

    for bot in CONTEXT_BOTS:
        subscription_json.append({"botId": bot, "chainId": CHAIN_ID})
        if CHAIN_ID in [10, 42161]:
            subscription_json.append({"botId": bot, "chainId": 1})

    subscription_json.append({"botId": ENTITY_CLUSTER_BOT, "alertId": ENTITY_CLUSTER_BOT_ALERT_ID, "chainId": CHAIN_ID})

    if CHAIN_ID in [10, 42161]:
        subscription_json.append({"botId": ENTITY_CLUSTER_BOT, "alertId": ENTITY_CLUSTER_BOT_ALERT_ID, "chainId": 1})


    return {"alertConfig": {"subscriptions": subscription_json}}


def get_pot_attacker_addresses(alert_event: forta_agent.alert_event.AlertEvent) -> list:
    """
    this function returns the attacker addresses from the labels
    :param alert_event: alert event with labels and addresses arrray
    :return: attacker_addresses: list of attacker addresses
    """
    pot_attacker_addresses = []
    try:
        for label in alert_event.alert.labels:
            if label.label is not None and ('attack' in label.label.lower() or 'exploit' in label.label.lower() or 'scam' in label.label.lower()):
                pot_attacker_addresses.append(label.entity)
        logging.info(f"alert {alert_event.alert_hash} {alert_event.alert_id} - Analysing {len(pot_attacker_addresses)} pot attacker addresses obtained from labels")

        for key in alert_event.alert.metadata.keys():
            if key is not None and ('attack' in key.lower() or 'exploit' in key.lower() or 'scam' in key.lower() or 'caller' in key.lower()):
                pot_address = alert_event.alert.metadata[key]
                if pot_address is not None and len(pot_address) == 42:
                    pot_attacker_addresses.append(pot_address.lower())
        logging.info(f"alert {alert_event.alert_hash} {alert_event.alert_id} - Analysing {len(pot_attacker_addresses)} pot attacker addresses obtained from labels and metadata")

    except Exception as e:
        logging.warning(f"alert {alert_event.alert_hash} {alert_event.alert_id} - Exception in get_pot_attacker_addresses from labels: {e} - {traceback.format_exc()}")

    if len(pot_attacker_addresses) == 0:
        logging.info(f"alert {alert_event.alert_hash} {alert_event.alert_id} - No attack labels in alert. Using addresses field.")
        pot_attacker_addresses = alert_event.alert.addresses
        logging.info(f"alert {alert_event.alert_hash} {alert_event.alert_id} - Analysing {len(pot_attacker_addresses)} pot attacker addresses obtained from addresses field")

    return pot_attacker_addresses


def get_etherscan_label(address: str):
    if address is None:
        return ""
        
    try:
        res = requests.get(label_api + address.lower())
        if res.status_code == 200:
            labels = res.json()
            if len(labels) > 0:
                return labels['events'][0]['label']['label']
    except Exception as e:
        logging.warning(f"Exception in get_etherscan_label {e}")
        return ""


def is_contract(w3, addresses) -> bool:
    """
    this function determines whether address/ addresses is a contract; if all are contracts, returns true; otherwise false
    :return: is_contract: bool
    """
    global CONTRACT_CACHE

    if addresses is None:
        return True

    if CONTRACT_CACHE.get(addresses) is not None:
        return CONTRACT_CACHE[addresses]
    else:
        is_contract = True
        for address in addresses.split(','):
            try:
                code = w3.eth.get_code(Web3.toChecksumAddress(address))
            except Exception as e:
                logging.error(f"Exception in is_contract {e}")

            is_contract = is_contract & (code != HexBytes('0x'))
        CONTRACT_CACHE[addresses] = is_contract
        return is_contract


def is_address(w3, addresses: str) -> bool:
    """
    this function determines whether address is a valid address
    :return: is_address: bool
    """
    if addresses is None:
        return True

    is_address = True
    for address in addresses.split(','):
        for c in ['a', 'b', 'c', 'd', 'e', 'f', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9']:
            test_str = c + c + c + c + c + c + c + c + c  # make a string of length 9; I know this is ugly, but regex didnt work
            if test_str in address.lower():
                is_address = False

    return is_address

def get_loss_info(alert_data: pd.DataFrame, context: dict) -> str:
    loss_info = ''

    df_intersection = alert_data[alert_data["transaction_hash"].isin(context.keys())]

    for i, row in df_intersection.iterrows():
        tx_hash = row["transaction_hash"]
        stage = row["stage"]
        if tx_hash in context.keys() and stage == "Exploitation":
            for c in context[tx_hash]:
                if c['bot_type'] == "profit":
                    profit_metadata = c
                    if "profit1" in profit_metadata.keys():
                        loss_info = "Loss of " + str(profit_metadata["profit1"])
                        return loss_info

    return loss_info

def get_victim_info(alert_data: pd.DataFrame, context: dict):
    victim_address, victim_name = "", ""
    victim_metadata = dict()

    df_intersection = alert_data[alert_data["transaction_hash"].isin(context.keys())]

    for i, row in df_intersection.iterrows():
        tx_hash = row["transaction_hash"]
        if tx_hash in context.keys():
            for c in context[tx_hash]:
                if c['bot_type'] == "victim":
                    victim_metadata = c
                    victim_address = victim_metadata["address1"] if "address1" in victim_metadata.keys() else ""
                    victim_name = victim_metadata["tag1"] if "tag1" in victim_metadata.keys() else ""
                    return victim_address, victim_name, victim_metadata

    return victim_address, victim_name, victim_metadata


def get_anomaly_score(alert_event: forta_agent.alert_event.AlertEvent) -> float:
    global DEFAULT_ANOMALY_SCORE

    anomaly_score = 1.0
    if alert_event.alert.metadata is not None and "anomaly_score" in alert_event.alert.metadata.keys():
        anomaly_score_str = alert_event.alert.metadata["anomaly_score"]
    elif alert_event.alert.metadata is not None and "anomalyScore" in alert_event.alert.metadata.keys():
        anomaly_score_str = alert_event.alert.metadata["anomalyScore"]
    else:
        logging.warning(f"alert {alert_event.alert_hash} {alert_event.alert_id} - no anomaly_score in metadata found: {alert_event.alert.metadata}. Treating as anomaly_score of 1.0.")
        anomaly_score_str = "1.0"

    anomaly_score = float(anomaly_score_str)
    if anomaly_score <= 0.0:
        anomaly_score = DEFAULT_ANOMALY_SCORE
        logging.warning(f"alert {alert_event.alert_hash} - anomaly_score is less or equal than 0.0. Treating as anomaly_score of {DEFAULT_ANOMALY_SCORE}.")
    elif anomaly_score > 1.0:
        anomaly_score = 1.0
        logging.warning(f"alert {alert_event.alert_hash} - anomaly_score is greater than 1.0. Treating as anomaly_score of 1.0.")

    return anomaly_score

def is_polygon_validator(w3, cluster: str, tx_hash: str) -> bool:
    if CHAIN_ID == 137:
        tx = w3.eth.get_transaction_receipt(tx_hash)
        for log in tx['logs']:
            if len(log['topics']) > 3:
                if log['topics'][0] == HexBytes('0x4dfe1bbbcf077ddc3e01291eea2d5c70c2b422b415d95645b9adcfd678cb1d63'):  # logfeetransfer event
                    validator = log['topics'][3].hex()[-40:]  # validator in 3rd pos
                    if validator in cluster:
                        return True
    return False

def get_end_user_attack_addresses(alert_event: forta_agent.alert_event.AlertEvent) -> list:
    addresses = set()

    #0xc608f1aff80657091ad14d974ea37607f6e7513fdb8afaa148b3bff5ba305c15,HARD-RUG-PULL-1,metadata,,attacker_deployer_address,
    #0xc608f1aff80657091ad14d974ea37607f6e7513fdb8afaa148b3bff5ba305c15,HARD-RUG-PULL-1,metadata,,attackerDeployerAddress,
    #0xf234f56095ba6c4c4782045f6d8e95d22da360bdc41b75c0549e2713a93231a4,SOFT-RUG-PULL-,metadata,,deployer,
    #0x36be2983e82680996e6ccc2ab39a506444ab7074677e973136fa8d914fc5dd11,RAKE-TOKEN-CONTRACT-1,metadata,,attackerRakeTokenDeployer,
    #0x36be2983e82680996e6ccc2ab39a506444ab7074677e973136fa8d914fc5dd11,RAKE-TOKEN-CONTRACT-1,metadata,,attacker_rake_token_deployer,
    if alert_event.bot_id == '0xc608f1aff80657091ad14d974ea37607f6e7513fdb8afaa148b3bff5ba305c15':  # hard rug pull
        if alert_event.alert.metadata.get('attacker_deployer_address') is not None:
            addresses.add(alert_event.alert.metadata['attacker_deployer_address'].lower())
        if alert_event.alert.metadata.get('attackerDeployerAddress') is not None:
            addresses.add(alert_event.alert.metadata['attacker_deployer_address'].lower())
    elif alert_event.bot_id == '0xf234f56095ba6c4c4782045f6d8e95d22da360bdc41b75c0549e2713a93231a4':  # soft rug pull
        if alert_event.alert.metadata.get('deployer') is not None:
            addresses.add(alert_event.alert.metadata['deployer'].lower())
    elif alert_event.bot_id == '0x36be2983e82680996e6ccc2ab39a506444ab7074677e973136fa8d914fc5dd11':
        if alert_event.alert.metadata.get('attackerRakeTokenDeployer') is not None:
            addresses.add(alert_event.alert.metadata['attackerRakeTokenDeployer'].lower())
        if alert_event.alert.metadata.get('attacker_rake_token_deployer') is not None:
            addresses.add(alert_event.alert.metadata['attacker_rake_token_deployer'].lower())

    return list(addresses)


def detect_attack(w3, alert_event: forta_agent.alert_event.AlertEvent) -> list:
    """
    this function returns finding for any address with at least 3 alerts observed on that address; it will generate an anomaly score
    :return: findings: list
    """
    global ALERT_ID_STAGE_MAPPING

    global ALERTED_CLUSTERS_LOOSE
    global ALERTED_CLUSTERS_FP_MITIGATED
    global ALERTED_CLUSTERS_STRICT
    global ALERT_DATA
    global FP_MITIGATION_CLUSTERS
    global END_USER_ATTACK_CLUSTERS
    global CONTEXT
    global ENTITY_CLUSTERS
    global CHAIN_ID
    global HIGHLY_PRECISE_BOTS

    findings = []
    try:
        start = time.time()

        chain_id = int(alert_event.alert.source.block.chain_id) if alert_event.alert.source.block.chain_id is not None else int(alert_event.chain_id)
        if chain_id == CHAIN_ID or (CHAIN_ID in [10, 42161] and chain_id == 1):
            logging.info(f"alert {alert_event.alert_hash} received for proper chain {chain_id}")

            #  assess whether we generate a finding
            #  note, only one instance will be running at a time to keep up with alert volume
            try:

                # update entity clusters
                if in_list(alert_event, [(ENTITY_CLUSTER_BOT, ENTITY_CLUSTER_BOT_ALERT_ID)]):
                    logging.info(f"alert {alert_event.alert_hash} is entity cluster alert")
                    cluster = alert_event.alert.metadata["entityAddresses"].lower()

                    for address in cluster.split(','):
                        ENTITY_CLUSTERS[address] = cluster
                        logging.info(f"alert {alert_event.alert_hash} - adding cluster mapping: {address} -> {cluster}")
                        while len(ENTITY_CLUSTERS) > ENTITY_CLUSTERS_MAX_QUEUE_SIZE:
                            ENTITY_CLUSTERS.pop(next(iter(ENTITY_CLUSTERS)))
                        logging.info(f"alert {alert_event.alert_hash} entity clusters size now: {len(ENTITY_CLUSTERS)}")

                        if ALERT_DATA.get(address) is not None:
                            alert_data = ALERT_DATA.pop(address)
                            if ALERT_DATA.get(cluster) is not None:
                                alert_data = pd.concat([alert_data, ALERT_DATA[cluster]], ignore_index=True, axis=0)
                            ALERT_DATA[cluster] = alert_data
                            logging.info(f"alert {alert_event.alert_hash} alert data size now: {len(ALERT_DATA)}")

                        if address in FP_MITIGATION_CLUSTERS:
                            FP_MITIGATION_CLUSTERS.append(cluster)
                            logging.info(f"alert {alert_event.alert_hash} FP mitigation clusters size now: {len(FP_MITIGATION_CLUSTERS)}")

                        if address in END_USER_ATTACK_CLUSTERS:
                            END_USER_ATTACK_CLUSTERS.append(cluster)
                            logging.info(f"alert {alert_event.alert_hash} end user attacks clusters size now: {len(END_USER_ATTACK_CLUSTERS)}")

                # update context alerts; currently either victim or large profit
                if (in_list(alert_event, CONTEXT_BOTS)):
                    logging.info(f"alert {alert_event.alert_hash} is a context alert")
                    logging.info(f"alert {alert_event.alert_hash} adding context list; size now: {len(CONTEXT)}")
                    alert_event.alert.metadata["bot_type"] = "victim" if alert_event.alert.source.bot.id == '0x441d3228a68bbbcf04e6813f52306efcaf1e66f275d682e62499f44905215250' else 'profit'
                    if CONTEXT.get(alert_event.alert.source.transaction_hash) is None:
                        CONTEXT[alert_event.alert.source.transaction_hash] = list()
                    CONTEXT[alert_event.alert.source.transaction_hash].append(alert_event.alert.metadata)

                    while len(CONTEXT) > CONTEXT_QUEUE_MAX_SIZE:
                        CONTEXT.pop(next(iter(CONTEXT)))

                # update FP mitigation clusters
                if in_list(alert_event, FP_MITIGATION_BOTS):
                    logging.info(f"alert {alert_event.alert_hash} is a FP mitigation alert")
                    address = alert_event.alert.description[0:42]
                    cluster = address
                    if address in ENTITY_CLUSTERS.keys():
                        cluster = ENTITY_CLUSTERS[address]
                    update_list(FP_MITIGATION_CLUSTERS, FP_CLUSTERS_QUEUE_MAX_SIZE, cluster)
                    logging.info(f"alert {alert_event.alert_hash} adding FP mitigation cluster: {cluster}. FP mitigation clusters size now: {len(FP_MITIGATION_CLUSTERS)}")

                # update end user clusters
                if in_list(alert_event, END_USER_ATTACK_BOTS):
                    logging.info(f"alert {alert_event.alert_hash} is an end user alert")
                    addresses = get_end_user_attack_addresses(alert_event)
                    for address in addresses:
                        cluster = address
                        if address in ENTITY_CLUSTERS.keys():
                            cluster = ENTITY_CLUSTERS[address]
                        update_list(END_USER_ATTACK_CLUSTERS, END_USER_ATTACK_CLUSTERS_QUEUE_MAX_SIZE, cluster)
                        logging.info(f"alert {alert_event.alert_hash} adding end user attacks cluster: {cluster}. End user attack clusters size now: {len(END_USER_ATTACK_CLUSTERS)}")


                # update alerts and process them for a given cluster
                if in_list(alert_event, BASE_BOTS):
                    logging.info(f"alert {alert_event.alert_hash}: is a base bot {alert_event.alert.source.bot.id}, {alert_event.alert_id} alert for addresses {alert_event.alert.addresses}")
                    end_date = datetime.strptime(alert_event.alert.created_at[0:25]+'Z', '%Y-%m-%dT%H:%M:%S.%fZ')  # getting block time stamp would be more accurate, but more expensive as it requires an RPC call
                    start_date = end_date - timedelta(hours=ALERTS_LOOKBACK_WINDOW_IN_HOURS)

                    # analyze attacker addresses from labels if there are any; otherwise analyze all addresses
                    pot_attacker_addresses = get_pot_attacker_addresses(alert_event)


                    for address in pot_attacker_addresses:
                        logging.info(f"alert {alert_event.alert_hash} - Analysing address {address}")
                        address_lower = address.lower()
                        cluster = address_lower
                        if address_lower in ENTITY_CLUSTERS.keys():
                            cluster = ENTITY_CLUSTERS[address_lower]
                        if(not is_address(w3, cluster)):  # ignore contracts and invalid addresses like 0x0000000000000blabla
                            logging.info(f"alert {alert_event.alert_hash}: {cluster} is not an address. Continue ... ")
                            continue

                        logging.info(f"alert {alert_event.alert_hash}: {cluster} is valid EOA.")

                        alert_anomaly_score = get_anomaly_score(alert_event)

                        stage = ALERT_ID_STAGE_MAPPING[(alert_event.bot_id, alert_event.alert.alert_id)]
                        logging.info(f"alert {alert_event.alert_hash} {alert_event.bot_id} {alert_event.alert.alert_id} {stage}: {cluster} anomaly score of {alert_anomaly_score}")

                        if ALERT_DATA.get(cluster) is None:
                            if CHAIN_ID in [10, 42161]:
                                ALERT_DATA[cluster] = pd.DataFrame(columns=['stage', 'created_at', 'anomaly_score', 'alert_hash', 'bot_id', 'alert_id', 'chain_id', 'addresses', 'transaction_hash'])
                            else:
                                ALERT_DATA[cluster] = pd.DataFrame(columns=['stage', 'created_at', 'anomaly_score', 'alert_hash', 'bot_id', 'alert_id', 'addresses', 'transaction_hash'])
                        alert_data = ALERT_DATA[cluster]
                        stage = ALERT_ID_STAGE_MAPPING[(alert_event.bot_id, alert_event.alert.alert_id)]
                        if CHAIN_ID in [10, 42161]:
                            alert_data = pd.concat([alert_data, pd.DataFrame([[stage, datetime.strptime(alert_event.alert.created_at[:-4] + 'Z', "%Y-%m-%dT%H:%M:%S.%fZ"), alert_anomaly_score, alert_event.alert_hash, alert_event.bot_id, alert_event.alert.alert_id, chain_id, alert_event.alert.addresses, alert_event.alert.source.transaction_hash]], columns=['stage', 'created_at', 'anomaly_score', 'alert_hash', 'bot_id', 'alert_id', 'chain_id', 'addresses', 'transaction_hash'])], ignore_index=True, axis=0)
                        else:
                            alert_data = pd.concat([alert_data, pd.DataFrame([[stage, datetime.strptime(alert_event.alert.created_at[:-4] + 'Z', "%Y-%m-%dT%H:%M:%S.%fZ"), alert_anomaly_score, alert_event.alert_hash, alert_event.bot_id, alert_event.alert.alert_id, alert_event.alert.addresses, alert_event.alert.source.transaction_hash]], columns=['stage', 'created_at', 'anomaly_score', 'alert_hash', 'bot_id', 'alert_id', 'addresses', 'transaction_hash'])], ignore_index=True, axis=0)
                        logging.info(f"alert {alert_event.alert_hash} - alert data size for cluster {cluster} now: {len(alert_data)}")

                        # add new alert and purge old alerts
                        ALERT_DATA[cluster] = alert_data[alert_data['created_at'] > start_date]
                        alert_data = ALERT_DATA[cluster]
                        logging.info(f"alert {alert_event.alert_hash} - alert data size for cluster {cluster} now (after date pruning): {len(alert_data)}")

                        # 3. contains highly precise bot
                        highly_precise_bot_count = 0
                        uniq_bot_alert_ids = alert_data[['bot_id', 'alert_id']].drop_duplicates(inplace=False)
                        for bot_id, alert_id, s in HIGHLY_PRECISE_BOTS:
                            if len(uniq_bot_alert_ids[(uniq_bot_alert_ids['bot_id'] == bot_id) & (uniq_bot_alert_ids['alert_id'] == alert_id)]) > 0:
                                highly_precise_bot_count += 1

                        # analyze ALERT_DATA to see whether conditions are met to generate a finding
                        # 1. Have to have at least MIN_ALERTS_COUNT bots reporting alerts
                        if len(alert_data['bot_id'].drop_duplicates(inplace=False)) >= MIN_ALERTS_COUNT or highly_precise_bot_count>0:
                            # 2. Have to have overall anomaly score of less than ANOMALY_SCORE_THRESHOLD
                            anomaly_scores_by_stages = alert_data[['stage', 'anomaly_score']].drop_duplicates(inplace=False)
                            anomaly_scores = anomaly_scores_by_stages.groupby('stage').min()
                            anomaly_score = anomaly_scores['anomaly_score'].prod()
                            logging.info(f"alert {alert_event.alert_hash} - Have sufficient number of alerts for {cluster}. Overall anomaly score is {anomaly_score}, {len(anomaly_scores)} stages.")
                            logging.info(f"alert {alert_event.alert_hash} - {cluster} anomaly scores {anomaly_scores}.")

                            if anomaly_score < ANOMALY_SCORE_THRESHOLD_LOOSE or len(anomaly_scores) == 4 or (highly_precise_bot_count>0 and len(anomaly_scores)>1):
                                logging.info(f"alert {alert_event.alert_hash} - Overall anomaly score for {cluster} is below threshold, 4 stages, or highly precise bot with 2 stages have been observed. Unless FP mitigation kicks in, will raise finding.")
                                if CHAIN_ID in [10, 42161] and alert_data[alert_data['chain_id'] == CHAIN_ID].empty:
                                    logging.info(f"No alert on chain {CHAIN_ID} for {cluster}. Wont raise finding")
                                    continue

                                fp_mitigated = False
                                end_user_attack = False
                                if(is_contract(w3, cluster)):
                                    logging.info(f"alert {alert_event.alert_hash} - {cluster} is contract. Wont raise finding")
                                    continue

                                etherscan_label = get_etherscan_label(cluster).lower()
                                if not ('attack' in etherscan_label
                                        or 'phish' in etherscan_label
                                        or 'hack' in etherscan_label
                                        or 'heist' in etherscan_label
                                        or 'exploit' in etherscan_label
                                        or 'scam' in etherscan_label
                                        or 'fraud' in etherscan_label
                                        or etherscan_label == ''):
                                    logging.info(f"alert {alert_event.alert_hash} -  Non attacker etherscan FP mitigation label {etherscan_label} for cluster {cluster}.")
                                    fp_mitigated = True

                                if (CHAIN_ID == 137 and len(alert_data) > POLYGON_VALIDATOR_ALERT_COUNT_THRESHOLD) or is_polygon_validator(w3, cluster, alert_event.alert.source.block.number):
                                    logging.info(f"alert {alert_event.alert_hash} - {cluster} is polygon validator. Wont raise finding")
                                    fp_mitigated = True

                                if cluster in FP_MITIGATION_CLUSTERS:
                                    logging.info(f"alert {alert_event.alert_hash} - Mitigating FP for {cluster}. Wont raise finding")
                                    fp_mitigated = True

                                if cluster in END_USER_ATTACK_CLUSTERS:
                                    logging.info(f"alert {alert_event.alert_hash} - End user attack identified for {cluster}. Downgrade finding")
                                    end_user_attack = True

                                victim_address, victim_name, victim_metadata = get_victim_info(alert_data, CONTEXT)
                                loss_info = get_loss_info(alert_data, CONTEXT)

                                if not end_user_attack and not fp_mitigated and (len(anomaly_scores) == 4) and cluster not in ALERTED_CLUSTERS_STRICT:
                                    logging.info(f"alert {alert_event.alert_hash} -1 critical severity finding for {cluster}. Anomaly score is {anomaly_score}.")
                                    update_list(ALERTED_CLUSTERS_STRICT, ALERTED_CLUSTERS_MAX_QUEUE_SIZE, cluster)
                                    findings.append(AlertCombinerFinding.create_finding(block_chain_indexer, cluster, victim_address, victim_name, loss_info, anomaly_score, FindingSeverity.Critical, "ATTACK-DETECTOR-1", alert_event, alert_data, victim_metadata, anomaly_scores_by_stages, CHAIN_ID))
                                elif not end_user_attack and not fp_mitigated and ((highly_precise_bot_count > 0 and len(anomaly_scores) > 1) or (highly_precise_bot_count > 1)) and cluster not in ALERTED_CLUSTERS_STRICT:
                                    logging.info(f"alert {alert_event.alert_hash} -1 critical severity finding for {cluster}. Anomaly score is {anomaly_score}.")
                                    update_list(ALERTED_CLUSTERS_STRICT, ALERTED_CLUSTERS_MAX_QUEUE_SIZE, cluster)
                                    findings.append(AlertCombinerFinding.create_finding(block_chain_indexer, cluster, victim_address, victim_name, loss_info, anomaly_score, FindingSeverity.Critical, "ATTACK-DETECTOR-2", alert_event, alert_data, victim_metadata, anomaly_scores_by_stages, CHAIN_ID))
                                elif not end_user_attack and not fp_mitigated and (len(alert_data['bot_id'].drop_duplicates(inplace=False)) >= MIN_ALERTS_COUNT and anomaly_score < ANOMALY_SCORE_THRESHOLD_STRICT) and cluster not in ALERTED_CLUSTERS_STRICT:
                                    logging.info(f"alert {alert_event.alert_hash} -1 critical severity finding for {cluster}. Anomaly score is {anomaly_score}.")
                                    update_list(ALERTED_CLUSTERS_STRICT, ALERTED_CLUSTERS_MAX_QUEUE_SIZE, cluster)
                                    findings.append(AlertCombinerFinding.create_finding(block_chain_indexer, cluster, victim_address, victim_name, loss_info, anomaly_score, FindingSeverity.Critical, "ATTACK-DETECTOR-3", alert_event, alert_data, victim_metadata, anomaly_scores_by_stages, CHAIN_ID))
                                elif not end_user_attack and not fp_mitigated and (len(alert_data['bot_id'].drop_duplicates(inplace=False)) >= MIN_ALERTS_COUNT  and anomaly_score < ANOMALY_SCORE_THRESHOLD_LOOSE) and cluster not in ALERTED_CLUSTERS_LOOSE and cluster not in ALERTED_CLUSTERS_STRICT:
                                    logging.info(f"alert {alert_event.alert_hash} -1 low severity finding for {cluster}. Anomaly score is {anomaly_score}.")
                                    update_list(ALERTED_CLUSTERS_LOOSE, ALERTED_CLUSTERS_MAX_QUEUE_SIZE, cluster)
                                    findings.append(AlertCombinerFinding.create_finding(block_chain_indexer, cluster, victim_address, victim_name, loss_info, anomaly_score, FindingSeverity.Low, "ATTACK-DETECTOR-4", alert_event, alert_data, victim_metadata, anomaly_scores_by_stages, CHAIN_ID))
                                elif not end_user_attack and fp_mitigated and (cluster not in ALERTED_CLUSTERS_FP_MITIGATED) and (((len(anomaly_scores) == 4) and cluster not in ALERTED_CLUSTERS_STRICT) or ((highly_precise_bot_count > 0 and len(anomaly_scores) > 1) and cluster not in ALERTED_CLUSTERS_STRICT) or ((len(alert_data['bot_id'].drop_duplicates(inplace=False)) >= MIN_ALERTS_COUNT and anomaly_score < ANOMALY_SCORE_THRESHOLD_STRICT) and cluster not in ALERTED_CLUSTERS_STRICT)
                                                                                         or ((len(alert_data['bot_id'].drop_duplicates(inplace=False)) >= MIN_ALERTS_COUNT  and anomaly_score < ANOMALY_SCORE_THRESHOLD_LOOSE) and cluster not in ALERTED_CLUSTERS_LOOSE and cluster not in ALERTED_CLUSTERS_STRICT)):
                                    update_list(ALERTED_CLUSTERS_FP_MITIGATED, ALERTED_CLUSTERS_MAX_QUEUE_SIZE, cluster)
                                    findings.append(AlertCombinerFinding.create_finding(block_chain_indexer, cluster, victim_address, victim_name, loss_info, anomaly_score, FindingSeverity.Info, "ATTACK-DETECTOR-5", alert_event, alert_data, victim_metadata, anomaly_scores_by_stages, CHAIN_ID))
                                elif end_user_attack and not fp_mitigated and (cluster not in ALERTED_CLUSTERS_FP_MITIGATED) and (((len(anomaly_scores) == 4) and cluster not in ALERTED_CLUSTERS_STRICT) or ((highly_precise_bot_count > 0 and len(anomaly_scores) > 1) and cluster not in ALERTED_CLUSTERS_STRICT) or ((len(alert_data['bot_id'].drop_duplicates(inplace=False)) >= MIN_ALERTS_COUNT and anomaly_score < ANOMALY_SCORE_THRESHOLD_STRICT) and cluster not in ALERTED_CLUSTERS_STRICT)
                                                                                         or ((len(alert_data['bot_id'].drop_duplicates(inplace=False)) >= MIN_ALERTS_COUNT  and anomaly_score < ANOMALY_SCORE_THRESHOLD_LOOSE) and cluster not in ALERTED_CLUSTERS_LOOSE and cluster not in ALERTED_CLUSTERS_STRICT)):
                                    update_list(ALERTED_CLUSTERS_FP_MITIGATED, ALERTED_CLUSTERS_MAX_QUEUE_SIZE, cluster)
                                    findings.append(AlertCombinerFinding.create_finding(block_chain_indexer, cluster, victim_address, victim_name, loss_info, anomaly_score, FindingSeverity.Info, "ATTACK-DETECTOR-6", alert_event, alert_data, victim_metadata, anomaly_scores_by_stages, CHAIN_ID))
                                else:
                                    logging.info(f"alert {alert_event.alert_hash} - Not raising finding for {cluster}. Already alerted.")

            except Exception as e:
                logging.warning(f"alert {alert_event.alert_hash} - Exception in process_alert {alert_event.alert_hash}: {e} - {traceback.format_exc()}")
        else:
            logging.debug(f"alert {alert_event.alert_hash} received for incorrect chain {alert_event.chain_id}. This bot is for chain {CHAIN_ID}.")
            raise AssertionError(f"alert {alert_event.alert_hash} received for incorrect chain {alert_event.chain_id}. This bot is for chain {CHAIN_ID}.")

        end = time.time()
        logging.info(f"alert {alert_event.alert_hash} {alert_event.alert_id} {alert_event.chain_id} processing took {end - start} seconds")
    except Exception as e:
        logging.warning(f"alert {alert_event.alert_hash} - Exception in process_alert {alert_event.alert_hash}: {e} - {traceback.format_exc()}")
        if 'NODE_ENV' in os.environ and 'production' in os.environ.get('NODE_ENV'):
            logging.info(f"alert {alert_event.alert_hash} - Raising exception to expose error to scannode")
            raise e

    return findings


def update_list(items: list, max_size: int, item: str):

    items.append(item.lower())

    while len(items) > max_size:
        items.pop(0)  # remove oldest item


def in_list(alert_event: forta_agent.alert_event.AlertEvent, bots: list) -> bool:
    """
    this function returns True if the alert is from a bot in the bots tuple
    :return: bool
    """
    for item in bots:
        if type(item) is tuple:
            if alert_event.alert.source.bot.id == item[0] and alert_event.alert.alert_id == item[1]:
                return True
        else:
            if alert_event.alert.source.bot.id == item:
                return True

    return False


def persist_state():
    global ALERTS_DATA_KEY
    global ALERT_DATA
    global FP_MITIGATION_CLUSTERS_KEY
    global FP_MITIGATION_CLUSTERS
    global END_USER_ATTACK_CLUSTERS_KEY
    global END_USER_ATTACK_CLUSTERS
    global ALERTED_CLUSTERS_STRICT_KEY
    global ALERTED_CLUSTERS_STRICT
    global ALERTED_CLUSTERS_LOOSE_KEY
    global ALERTED_CLUSTERS_LOOSE
    global ALERTED_CLUSTERS_FP_MITIGATED_KEY
    global ALERTED_CLUSTERS_FP_MITIGATED
    global ENTITY_CLUSTERS_KEY
    global ENTITY_CLUSTERS
    global CHAIN_ID

    start = time.time()
    persist(ALERT_DATA, CHAIN_ID, ALERTS_DATA_KEY)
    persist(FP_MITIGATION_CLUSTERS, CHAIN_ID, FP_MITIGATION_CLUSTERS_KEY)
    persist(END_USER_ATTACK_CLUSTERS, CHAIN_ID, END_USER_ATTACK_CLUSTERS_KEY)
    persist(ENTITY_CLUSTERS, CHAIN_ID, ENTITY_CLUSTERS_KEY)
    persist(ALERTED_CLUSTERS_LOOSE, CHAIN_ID, ALERTED_CLUSTERS_LOOSE_KEY)
    persist(ALERTED_CLUSTERS_FP_MITIGATED, CHAIN_ID, ALERTED_CLUSTERS_FP_MITIGATED_KEY)
    persist(ALERTED_CLUSTERS_STRICT, CHAIN_ID, ALERTED_CLUSTERS_STRICT_KEY)
    end = time.time()
    logging.info(f"Persisted bot state. took {end - start} seconds")


def persist(obj: object, chain_id: int, key: str):
    L2Cache.write(obj, chain_id, key)


def load(chain_id: int, key: str) -> object:
    return L2Cache.load(chain_id, key)


def provide_handle_alert(w3):
    logging.debug("provide_handle_alert called")

    def handle_alert(alert_event: forta_agent.alert_event.AlertEvent) -> list:
        logging.debug("handle_alert inner called")

        findings = detect_attack(w3, alert_event)
        if not ('NODE_ENV' in os.environ and 'production' in os.environ.get('NODE_ENV')):
            persist_state()

        return findings

    return handle_alert


real_handle_alert = provide_handle_alert(web3)


def handle_alert(alert_event: forta_agent.alert_event.AlertEvent) -> list:
    logging.debug("handle_alert called")
    return real_handle_alert(alert_event)


def handle_block(block_event: forta_agent.BlockEvent):
    logging.debug("handle_block called")

    if datetime.now().minute == 0:  # every hour
        persist_state()

    return []
