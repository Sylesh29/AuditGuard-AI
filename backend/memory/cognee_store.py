import json
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# In-memory fallback store (always used as backing store; Cognee mirrors writes when available)
_memory_store: dict = {}
_cognee_available = False

async def init_cognee():
    """Initialize Cognee connection. Falls back to in-memory dict if unavailable."""
    global _cognee_available
    api_key = os.getenv("COGNEE_API_KEY", "")
    if not api_key or api_key == "your_cognee_api_key_here":
        logger.warning("COGNEE_API_KEY not set — using in-memory fallback store")
        _cognee_available = False
        return
    try:
        import cognee
        # Disable multi-user access control for single-user demo
        os.environ.setdefault("ENABLE_BACKEND_ACCESS_CONTROL", "false")
        os.environ.setdefault("CACHING", "false")
        # Cognee 1.x: configure LLM key
        try:
            cognee.config.set_llm_api_key(api_key)
        except Exception:
            pass
        # Attempt to reset storage for fresh session
        try:
            await cognee.prune.prune_system(metadata=True)
        except Exception:
            pass
        _cognee_available = True
        logger.info("Cognee initialized successfully (v1.x)")
    except Exception as e:
        logger.warning(f"Cognee init failed ({e}) — using in-memory fallback store")
        _cognee_available = False


async def write_memory(agent_name: str, data: dict) -> bool:
    """Write agent output to dict store (always) and Cognee (when available). Never crashes."""
    global _memory_store
    payload = {
        "agent_name": agent_name,
        "timestamp": datetime.utcnow().isoformat(),
        "data": data
    }
    # Always write to dict first (primary store — guarantees read works)
    _memory_store[agent_name] = payload
    logger.info(f"In-memory write OK: {agent_name}")

    # Mirror to Cognee if available
    if _cognee_available:
        try:
            import cognee
            text = json.dumps(payload)
            await cognee.add(text, dataset_name=f"auditguard_{agent_name}")
            await cognee.cognify()
            logger.info(f"Cognee mirror write OK: {agent_name}")
        except Exception as e:
            logger.warning(f"Cognee mirror write failed for {agent_name}: {e} (dict store has the data)")
    return True


async def read_memory(agent_name: str) -> dict | None:
    """Read a specific agent's output. Returns None if not found."""
    try:
        if _cognee_available:
            import cognee
            results = await cognee.search(
                f"auditguard {agent_name} agent output",
                query_type="CHUNKS"
            )
            if results:
                for r in results:
                    text = getattr(r, "text", None) or str(r)
                    try:
                        parsed = json.loads(text)
                        if parsed.get("agent_name") == agent_name:
                            return parsed.get("data")
                    except Exception:
                        continue
        return _memory_store.get(agent_name, {}).get("data")
    except Exception as e:
        logger.error(f"Memory read failed for {agent_name}: {e}")
        return _memory_store.get(agent_name, {}).get("data")


async def read_all_memory() -> dict:
    """Read all agent outputs. Returns dict keyed by agent_name."""
    result = {}
    for agent in ["scout", "ranker", "fixer", "narrator"]:
        data = await read_memory(agent)
        if data is not None:
            result[agent] = data
    return result


async def clear_session():
    """Clear all memory for a fresh audit run."""
    global _memory_store
    _memory_store = {}
    if _cognee_available:
        try:
            import cognee
            await cognee.prune.prune_system(metadata=True)
        except Exception as e:
            logger.warning(f"Cognee prune failed: {e}")
    logger.info("Session memory cleared")
