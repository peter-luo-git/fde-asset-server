"""模型网关客户端：OpenAI 兼容接口，谁在后面都行。

密钥只从环境变量读，不进数据库、不进资产库。本地开发可以写在仓库根的
`.env.local`（已 gitignore），服务启动时会把它加载进环境。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

DEFAULT_TIMEOUT = 30.0


def load_env_file(path: Path) -> None:
    """把 .env.local 加载进环境变量；已有的同名变量优先，不覆盖。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


@dataclass
class LlmConfig:
    """一家供应商的配置。生成和重排可以是两家，各自一套地址、密钥、模型名。"""

    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout: float = DEFAULT_TIMEOUT
    #: 额外请求参数，例如关掉思考 {"enable_thinking": false}
    extra_body: dict[str, Any] | None = None

    @property
    def usable(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


def _cfg(prefix: str, *, model_env: str, fallback_model_env: str = "") -> LlmConfig:
    model = os.environ.get(model_env, "")
    if not model and fallback_model_env:
        model = os.environ.get(fallback_model_env, "")
    return LlmConfig(
        base_url=os.environ.get(f"{prefix}_BASE_URL", "").rstrip("/"),
        api_key=os.environ.get(f"{prefix}_API_KEY", ""),
        model=model,
        timeout=float(os.environ.get("FDE_ASSET_LLM_TIMEOUT", DEFAULT_TIMEOUT)),
    )


def reason_config(model_override: str = "") -> LlmConfig:
    """写推荐理由用的生成模型。"""
    config = _cfg(
        "FDE_ASSET_LLM",
        model_env="FDE_ASSET_REASON_MODEL",
        # 兼容早先把生成模型写在 RERANK_MODEL 里的 .env.local
        fallback_model_env="FDE_ASSET_RERANK_MODEL",
    )
    if model_override:
        config.model = model_override
    return config


def rerank_config() -> LlmConfig:
    """排序用的 rerank 模型，可以是另一家供应商、另一个 token。"""
    config = _cfg("FDE_ASSET_RERANK", model_env="FDE_ASSET_RERANK_MODEL_NAME")
    if not config.base_url:
        # 没单独配就不启用，调用方退回生成模型或关键词排序
        return LlmConfig()
    return config


def config_from_env(model_override: str = "") -> LlmConfig:
    """向后兼容的旧名字。"""
    return reason_config(model_override)


class LlmClient(Protocol):
    def complete_json(self, system: str, user: str | list[dict[str, Any]]) -> dict[str, Any]: ...


class NullLlmClient:
    """没配模型时用它：调用方据此退回关键词排序。"""

    usable = False

    def complete_json(self, system: str, user: str) -> dict[str, Any]:  # pragma: no cover
        raise RuntimeError("没有配置模型网关")


class OpenAICompatibleClient:
    """走 /chat/completions，要求模型只吐 JSON。"""

    def __init__(self, config: LlmConfig) -> None:
        self.config = config

    @property
    def usable(self) -> bool:
        return self.config.usable

    def complete_json(self, system: str, user: str | list[dict[str, Any]]) -> dict[str, Any]:
        """user 可以是一段文字，也可以是多段内容（文字加图片 image_url），用于看图。"""
        import httpx

        response = httpx.post(
            f"{self.config.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
                **(self.config.extra_body or {}),
            },
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        text = payload["choices"][0]["message"]["content"]
        return _loads(text)


class FallbackLlmClient:
    """按顺序试几条线路，前一条出错（超时、5xx、返回不是 JSON）就换下一条。"""

    def __init__(self, clients: list[OpenAICompatibleClient]) -> None:
        self.clients = [client for client in clients if client.usable]

    @property
    def usable(self) -> bool:
        return bool(self.clients)

    def complete_json(self, system: str, user: str | list[dict[str, Any]]) -> dict[str, Any]:
        error: Exception | None = None
        for client in self.clients:
            try:
                return client.complete_json(system, user)
            except Exception as exc:  # noqa: BLE001 - 换下一条线路
                error = exc
        raise error or RuntimeError("没有配置模型网关")


def agent_model_client(timeout: float = 120.0) -> FallbackLlmClient:
    """和 Agent 用同一套模型（部署时把 agent-model.env 也给资产中心）：
    FDE_MODEL_* 是主线路，FDE_MODEL_FALLBACK_* 是备用线路，模型用快的那个（*_FAST）。"""
    configs = []
    for prefix in ("FDE_MODEL", "FDE_MODEL_FALLBACK"):
        try:
            extra = json.loads(os.environ.get(f"{prefix}_EXTRA_BODY") or "{}")
        except ValueError:
            extra = {}
        configs.append(
            LlmConfig(
                base_url=os.environ.get(f"{prefix}_BASE_URL", "").rstrip("/"),
                api_key=os.environ.get(f"{prefix}_API_KEY", ""),
                model=os.environ.get(f"{prefix}_FAST") or os.environ.get(f"{prefix}_CODER", ""),
                timeout=timeout,
                extra_body=extra if isinstance(extra, dict) else {},
            )
        )
    return FallbackLlmClient([OpenAICompatibleClient(config) for config in configs])


def _loads(text: str) -> dict[str, Any]:
    """模型偶尔会在 JSON 外面裹一层 ```json，这里剥掉再解析。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0]
    return json.loads(cleaned)


def build_client(model_override: str = "") -> LlmClient:
    config = config_from_env(model_override)
    return OpenAICompatibleClient(config) if config.usable else NullLlmClient()
