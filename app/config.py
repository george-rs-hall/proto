import os

from dotenv import load_dotenv

load_dotenv()

# Cognee reads these from the environment when it's imported, so import this module before `cognee`
COGNEE_CONFIG = {
    'LLM_PROVIDER': 'anthropic',
    'LLM_MODEL': 'claude-haiku-4-5',
    'EMBEDDING_PROVIDER': 'fastembed',
    'EMBEDDING_MODEL': 'BAAI/bge-small-en-v1.5',
    'EMBEDDING_DIMENSIONS': '384',
    'DATA_ROOT_DIRECTORY': './.cognee/data',
    'SYSTEM_ROOT_DIRECTORY': './.cognee/system',
    'AUTO_FEEDBACK': 'false',
}

os.environ.update(COGNEE_CONFIG)
