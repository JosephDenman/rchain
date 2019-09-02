import hashlib
import re
import sys
import time
from pathlib import Path
from random import Random
from typing import Generator, Tuple, Dict
from contextlib import contextmanager
import logging
import pytest
from docker.client import DockerClient
from rchain.crypto import PrivateKey, gen_block_hash_from_block, gen_deploys_hash_from_block
from rchain.pb.CasperMessage_pb2 import BlockMessage, Justification
from rchain.util import create_deploy_data


from . import conftest
from .common import (TestingContext,
                     CommandLineOptions)
from .node_client import (NodeClient,
                          node_protocol_client)
from .rnode import (Node,
                    bootstrap_connected_peer,
                    docker_network_with_started_bootstrap,
                    extract_validator_stake_from_bonds_validator_str)
from .wait import (wait_for_log_match,
                   wait_for_node_sees_block)


BOOTSTRAP_NODE_KEY = PrivateKey.from_hex("ff2ba092524bafdbc85fa0c7eddb2b41c69bc9bf066a4711a8a16f749199e5be")
BONDED_VALIDATOR_KEY_1 = PrivateKey.from_hex("597623f0b50e82008d52644983699724538b4307efbe3d0b992c651ca7f860c8")
BONDED_VALIDATOR_KEY_2 = PrivateKey.from_hex("9a32ff7b7c6e25527e0b4e5bec70596c6094e6529d56bf61cbd1ca26d3e92b10")
BONDED_VALIDATOR_KEY_3 = PrivateKey.from_hex("af47862137d4e772f540029ae73ee01443c61288f3df9307a13d681de6ad2de4")


def generate_block_hash() -> bytes:
    blake = hashlib.blake2b(digest_size=32)
    blake.update(b'evil')
    return blake.digest()


@contextmanager
def three_nodes_network_with_node_client(command_line_options: CommandLineOptions, random_generator: Random, docker_client: DockerClient, validator_bonds_dict: Dict[PrivateKey, int] = None) -> Generator[Tuple[TestingContext, Node, Node, Node, NodeClient], None, None]:
    peers_keypairs = [BONDED_VALIDATOR_KEY_1, BONDED_VALIDATOR_KEY_2]
    with conftest.testing_context(command_line_options, random_generator, docker_client, bootstrap_key=BOOTSTRAP_NODE_KEY, peers_keys=peers_keypairs, validator_bonds_dict=validator_bonds_dict) as context, \
        docker_network_with_started_bootstrap(context=context) as bootstrap_node, \
        bootstrap_connected_peer(context=context, bootstrap=bootstrap_node, name='bonded-validator-1', private_key=BONDED_VALIDATOR_KEY_1) as validator1, \
        bootstrap_connected_peer(context=context, bootstrap=bootstrap_node, name='bonded-validator-2', private_key=BONDED_VALIDATOR_KEY_2) as validator2, \
        node_protocol_client(bootstrap_node.network, docker_client, context) as client:
        yield context, bootstrap_node, validator1, validator2, client


@pytest.mark.skipif(sys.platform in ('win32', 'cygwin', 'darwin'), reason="Only Linux docker support connection between host and container which node client needs")
def test_simple_slash(command_line_options: CommandLineOptions, random_generator: Random, docker_client: DockerClient) -> None:
    with three_nodes_network_with_node_client(command_line_options, random_generator, docker_client) as  (context, _ , validator1, validator2, client):
        contract = '/opt/docker/examples/tut-hello.rho'

        validator1.deploy(contract, BONDED_VALIDATOR_KEY_1)
        blockhash = validator1.propose()

        wait_for_node_sees_block(context, validator2, blockhash)

        block_info = validator1.show_block_parsed(blockhash)

        block_msg = client.block_request(block_info['blockHash'], validator1)
        evil_block_hash = generate_block_hash()

        block_msg.blockHash = evil_block_hash
        block_msg.sig = BONDED_VALIDATOR_KEY_1.sign_block_hash(evil_block_hash)
        block_msg.header.timestamp = int(time.time()*1000)

        client.send_block(block_msg, validator2)

        record_invalid = re.compile("Recording invalid block {}... for InvalidBlockHash".format(evil_block_hash.hex()[:10]))
        wait_for_log_match(context, validator2, record_invalid)

        validator2.deploy(contract, BONDED_VALIDATOR_KEY_2)

        slashed_block_hash = validator2.propose()

        block_info = validator2.show_block_parsed(slashed_block_hash)
        bonds_validators = extract_validator_stake_from_bonds_validator_str(block_info['bondsValidatorList'])

        assert bonds_validators[BONDED_VALIDATOR_KEY_1.get_public_key().to_hex()] == 0

@pytest.mark.skipif(sys.platform in ('win32', 'cygwin', 'darwin'), reason="Only Linux docker support connection between host and container which node client needs")
def test_slash_invalid_block_seq(command_line_options: CommandLineOptions, random_generator: Random, docker_client: DockerClient) -> None:
    """
    Propose an block with invalid block seq number(a block seq number that isn't one more than the max of all the parents block's numbers).
    """
    with three_nodes_network_with_node_client(command_line_options, random_generator, docker_client) as  (context, _ , validator1, validator2, client):
        contract = '/opt/docker/examples/tut-hello.rho'

        validator1.deploy(contract, BONDED_VALIDATOR_KEY_1)
        blockhash = validator1.propose()

        wait_for_node_sees_block(context, validator2, blockhash)

        block_info = validator1.show_block_parsed(blockhash)

        block_msg = client.block_request(block_info['blockHash'], validator1)

        invalid_block_num_block = BlockMessage()
        invalid_block_num_block.CopyFrom(block_msg)
        invalid_block_num_block.seqNum = 1000
        # change timestamp to make block hash different
        invalid_block_num_block.header.timestamp = block_msg.header.timestamp + 1  # pylint: disable=maybe-no-member
        invalid_block_num_block.header.deploysHash = gen_deploys_hash_from_block(invalid_block_num_block)  # pylint: disable=maybe-no-member
        invalid_block_hash = gen_block_hash_from_block(invalid_block_num_block)
        invalid_block_num_block.sig = BONDED_VALIDATOR_KEY_1.sign_block_hash(invalid_block_hash)
        invalid_block_num_block.blockHash = invalid_block_hash
        logging.info("Invalid block {}".format(invalid_block_hash.hex()))
        client.send_block(invalid_block_num_block, validator2)
        record_invalid = re.compile("Recording invalid block {}... for InvalidSequenceNumber".format(invalid_block_hash.hex()[:10]))
        wait_for_log_match(context, validator2, record_invalid)

        validator2.deploy(contract, BONDED_VALIDATOR_KEY_2)

        slashed_block_hash = validator2.propose()

        block_info = validator2.show_block_parsed(slashed_block_hash)
        bonds_validators = extract_validator_stake_from_bonds_validator_str(block_info['bondsValidatorList'])

        assert bonds_validators[BONDED_VALIDATOR_KEY_1.get_public_key().to_hex()] == 0.0

@pytest.mark.skipif(sys.platform in ('win32', 'cygwin', 'darwin'), reason="Only Linux docker support connection between host and container which node client needs")
def test_slash_justification_not_correct(command_line_options: CommandLineOptions, random_generator: Random, docker_client: DockerClient) -> None:
    """
    Slash a validator which proposed a block with justifications not matching bonded validators of main parent
    """
    bonded_validators = {
        BOOTSTRAP_NODE_KEY: 100,
        BONDED_VALIDATOR_KEY_1: 100,
        BONDED_VALIDATOR_KEY_2: 100,
        BONDED_VALIDATOR_KEY_3: 100,
    }
    with three_nodes_network_with_node_client(command_line_options, random_generator, docker_client, validator_bonds_dict=bonded_validators) as  (context, _ , validator1, validator2, client):
        contract = '/opt/docker/examples/tut-hello.rho'

        validator1.deploy(contract, BONDED_VALIDATOR_KEY_1)
        blockhash = validator1.propose()

        wait_for_node_sees_block(context, validator2, blockhash)

        block_info = validator1.show_block_parsed(blockhash)

        block_msg = client.block_request(block_info['blockHash'], validator1)

        invalid_justifications_block = BlockMessage()
        invalid_justifications_block.CopyFrom(block_msg)
        error_justification = Justification(validator=PrivateKey.generate().to_bytes(), latestBlockHash=block_msg.blockHash)

        invalid_justifications_block.justifications.append(error_justification)  # pylint: disable=maybe-no-member
        # change timestamp to make block hash different
        invalid_justifications_block.header.timestamp = block_msg.header.timestamp + 1  # pylint: disable=maybe-no-member
        invalid_block_hash = gen_block_hash_from_block(invalid_justifications_block)
        invalid_justifications_block.sig = BONDED_VALIDATOR_KEY_1.sign_block_hash(invalid_block_hash)
        invalid_justifications_block.blockHash = invalid_block_hash
        client.send_block(invalid_justifications_block, validator2)

        record_invalid = re.compile("Recording invalid block {}... for InvalidFollows".format(invalid_block_hash.hex()[:10]))
        wait_for_log_match(context, validator2, record_invalid)

        validator2.deploy(contract, BONDED_VALIDATOR_KEY_2)
        slashed_block_hash = validator2.propose()

        block_info = validator2.show_block_parsed(slashed_block_hash)
        bonds_validators = extract_validator_stake_from_bonds_validator_str(block_info['bondsValidatorList'])

        assert bonds_validators[BONDED_VALIDATOR_KEY_1.get_public_key().to_hex()] == 0.0


@pytest.mark.skipif(sys.platform in ('win32', 'cygwin', 'darwin'), reason="Only Linux docker support connection between host and container which node client needs")
def test_slash_invalid_validator_approve_evil_block(command_line_options: CommandLineOptions, random_generator: Random, docker_client: DockerClient) -> None:
    """Slash a validator who doesn't slash invalid block

    1.v1 proposes valid block
    2.v1 creates another block with invalid parent block hash and sends it to v2
    3.v2 creates block using v1's second block as parent hash (i.e. no slashing, another invalid block) and sends it to v3
    4.v3 records invalid block (InvalidTransaction) from v2
    5.v3 proposes block which slashes both v1 and v2
    """
    with three_nodes_network_with_node_client(command_line_options, random_generator, docker_client) as  (context, validator3 , validator1, validator2, client):

        genesis_block = validator3.show_blocks_parsed(2)[0]

        contract = '/opt/docker/examples/tut-hello.rho'

        validator1.deploy(contract, BONDED_VALIDATOR_KEY_1)
        blockhash = validator1.propose()

        wait_for_node_sees_block(context, validator3, blockhash)
        wait_for_node_sees_block(context, validator2, blockhash)

        block_info = validator1.show_block_parsed(blockhash)

        block_msg = client.block_request(block_info['blockHash'], validator1)

        evil_block_hash = generate_block_hash()

        # invalid block from validator1
        invalid_block = BlockMessage()
        invalid_block.CopyFrom(block_msg)
        invalid_block.seqNum = block_msg.seqNum + 1
        invalid_block.body.state.blockNumber = block_msg.body.state.blockNumber + 1  # pylint: disable=maybe-no-member
        invalid_block.blockHash = evil_block_hash
        invalid_block.header.timestamp = int(time.time()*1000)  # pylint: disable=maybe-no-member
        invalid_block.sig = BONDED_VALIDATOR_KEY_1.sign_block_hash(evil_block_hash)
        invalid_block.header.ClearField("parentsHashList")  # pylint: disable=maybe-no-member
        invalid_block.header.parentsHashList.append(bytes.fromhex(genesis_block['blockHash']))  # pylint: disable=maybe-no-member
        invalid_block.ClearField("justifications")
        invalid_block.justifications.extend([  # pylint: disable=maybe-no-member
            Justification(validator=BONDED_VALIDATOR_KEY_1.get_public_key().to_bytes(), latestBlockHash=block_msg.blockHash),
            Justification(validator=BONDED_VALIDATOR_KEY_2.get_public_key().to_bytes(), latestBlockHash=bytes.fromhex(genesis_block['blockHash'])),
            Justification(validator=BOOTSTRAP_NODE_KEY.get_public_key().to_bytes(), latestBlockHash=bytes.fromhex(genesis_block['blockHash'])),
        ])
        client.send_block(invalid_block, validator2)

        wait_for_node_sees_block(context, validator2, evil_block_hash.hex())

        # block which is created by validator2 but not slashing validator1
        block_not_slash_invalid_block = BlockMessage()
        block_not_slash_invalid_block.CopyFrom(block_msg)
        block_not_slash_invalid_block.seqNum = 1
        block_not_slash_invalid_block.body.state.blockNumber = 1  # pylint: disable=maybe-no-member
        block_not_slash_invalid_block.sender = BONDED_VALIDATOR_KEY_2.get_public_key().to_bytes()
        block_not_slash_invalid_block.ClearField("justifications")
        block_not_slash_invalid_block.justifications.extend([  # pylint: disable=maybe-no-member
            Justification(validator=BONDED_VALIDATOR_KEY_1.get_public_key().to_bytes(), latestBlockHash=evil_block_hash),
            Justification(validator=BONDED_VALIDATOR_KEY_2.get_public_key().to_bytes(), latestBlockHash=bytes.fromhex(genesis_block['blockHash'])),
            Justification(validator=BOOTSTRAP_NODE_KEY.get_public_key().to_bytes(), latestBlockHash=bytes.fromhex(genesis_block['blockHash'])),
        ])
        deploy_data = create_deploy_data(BONDED_VALIDATOR_KEY_2, Path("../rholang/examples/tut-hello.rho").read_text(), 1, 1000000)
        block_not_slash_invalid_block.body.deploys[0].deploy.CopyFrom(deploy_data)  # pylint: disable=maybe-no-member
        block_not_slash_invalid_block.header.ClearField("parentsHashList")  # pylint: disable=maybe-no-member
        block_not_slash_invalid_block.header.parentsHashList.append(bytes.fromhex(genesis_block['blockHash']))  # pylint: disable=maybe-no-member
        block_not_slash_invalid_block.header.timestamp = int(time.time()*1000)  # pylint: disable=maybe-no-member
        block_not_slash_invalid_block.header.deploysHash = gen_deploys_hash_from_block(block_not_slash_invalid_block)  # pylint: disable=maybe-no-member
        invalid_block_hash = gen_block_hash_from_block(block_not_slash_invalid_block)
        block_not_slash_invalid_block.sig = BONDED_VALIDATOR_KEY_2.sign_block_hash(invalid_block_hash)
        block_not_slash_invalid_block.blockHash = invalid_block_hash

        client.send_block(block_not_slash_invalid_block, validator3)

        # Because validator2 doesn't slash validator1's block while validator3 slash validator1's block,
        # hence there are some comm events lack of in validator3 which cause an invalid transaction
        record_invalid = re.compile("Recording invalid block {}... for InvalidTransaction".format(invalid_block_hash.hex()[:10]))
        wait_for_log_match(context, validator3, record_invalid)


        validator3.deploy(contract, BOOTSTRAP_NODE_KEY)
        slashed_blockhash = validator3.propose()
        slashed_block_info = validator2.show_block_parsed(slashed_blockhash)
        bonds_validators = extract_validator_stake_from_bonds_validator_str(slashed_block_info['bondsValidatorList'])

        assert bonds_validators[BONDED_VALIDATOR_KEY_1.get_public_key().to_hex()] == 0
        assert bonds_validators[BONDED_VALIDATOR_KEY_2.get_public_key().to_hex()] == 0