import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

COGNEE_DIR = Path(__file__).resolve().parent.parent / '.cognee'

# Cognee reads these from the environment when it's imported, so import this module before `cognee`
COGNEE_CONFIG = {
    'LLM_PROVIDER': 'anthropic',
    'LLM_MODEL': 'claude-haiku-4-5',
    'EMBEDDING_PROVIDER': 'fastembed',
    'EMBEDDING_MODEL': 'BAAI/bge-small-en-v1.5',
    'EMBEDDING_DIMENSIONS': '384',
    'DATA_ROOT_DIRECTORY': str(COGNEE_DIR / 'data'),
    'SYSTEM_ROOT_DIRECTORY': str(COGNEE_DIR / 'system'),
    'AUTO_FEEDBACK': 'false',
}

os.environ.update(COGNEE_CONFIG)
