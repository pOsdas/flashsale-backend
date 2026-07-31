import json
from collections.abc import Mapping
from typing import Any

import httpx
from django.conf import settings


class TelegramApiError(RuntimeError):
    """Telegram Bot API returned an invalid or unsuccessful response."""


class TelegramBotClient:
    def __init__(
        self,
        *,
        token: str,
        timeout_seconds: int = 30,
        base_url: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        normalized_token = token.strip()

        if not normalized_token:
            raise TelegramApiError("Telegram bot token is empty")

        self.token = normalized_token
        self.timeout_seconds = timeout_seconds
        telegram_api_base_url = (
            base_url
            or getattr(
                settings,
                "NOTIF_TELEGRAM_API_BASE_URL",
                "https://api.telegram.org",
            )
        ).rstrip("/")
        self.base_url = (
            f"{telegram_api_base_url}/bot{normalized_token}"
        )
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(
                timeout_seconds + 5,
                connect=10,
                read=timeout_seconds + 5,
                write=10,
                pool=10,
            ),
            trust_env=False,
        )

    def drop_pending_updates(self) -> None:
        self._post(
            path="/deleteWebhook",
            payload={
                "drop_pending_updates": True,
            },
        )

    def get_updates(
        self,
        *,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "timeout": self.timeout_seconds,
            "allowed_updates": json.dumps(
                [
                    "message",
                    "callback_query",
                ]
            ),
        }

        if offset is not None:
            params["offset"] = offset

        result = self._get(
            path="/getUpdates",
            params=params,
        )

        if not isinstance(result, list):
            raise TelegramApiError(
                "Telegram getUpdates result must be a list"
            )

        return [
            update
            for update in result
            if isinstance(update, dict)
        ]

    def set_my_commands(
        self,
        *,
        commands: tuple[dict[str, str], ...],
    ) -> bool:
        normalized_commands: list[dict[str, str]] = []

        for command_data in commands:
            command = str(
                command_data.get("command") or ""
            ).strip()
            description = str(
                command_data.get("description") or ""
            ).strip()

            if not command:
                raise TelegramApiError(
                    "Telegram bot command is empty"
                )

            if not description:
                raise TelegramApiError(
                    "Telegram bot command description is empty"
                )

            normalized_commands.append(
                {
                    "command": command,
                    "description": description,
                }
            )

        if not normalized_commands:
            raise TelegramApiError(
                "Telegram bot commands are empty"
            )

        result = self._post(
            path="/setMyCommands",
            payload={
                "commands": normalized_commands,
            },
        )

        if not isinstance(result, bool):
            raise TelegramApiError(
                "Telegram setMyCommands result must be boolean"
            )

        return result

    def send_message(
        self,
        *,
        chat_id: int | str,
        text: str,
        reply_markup: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_text = text.strip()

        if not normalized_text:
            raise TelegramApiError("Telegram message text is empty")

        payload: dict[str, Any] = {
            "chat_id": self._normalize_chat_id(chat_id),
            "text": normalized_text,
            "disable_web_page_preview": True,
        }

        if reply_markup is not None:
            payload["reply_markup"] = dict(reply_markup)

        result = self._post(
            path="/sendMessage",
            payload=payload,
        )

        return self._ensure_object_result(
            method_name="sendMessage",
            result=result,
        )

    def edit_message_text(
        self,
        *,
        chat_id: int | str,
        message_id: int,
        text: str,
        reply_markup: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_text = text.strip()

        if not normalized_text:
            raise TelegramApiError("Telegram message text is empty")

        payload: dict[str, Any] = {
            "chat_id": self._normalize_chat_id(chat_id),
            "message_id": int(message_id),
            "text": normalized_text,
            "disable_web_page_preview": True,
        }

        if reply_markup is not None:
            payload["reply_markup"] = dict(reply_markup)

        result = self._post(
            path="/editMessageText",
            payload=payload,
        )

        return self._ensure_object_result(
            method_name="editMessageText",
            result=result,
        )

    def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> bool:
        normalized_callback_query_id = callback_query_id.strip()

        if not normalized_callback_query_id:
            raise TelegramApiError(
                "Telegram callback_query_id is empty"
            )

        payload: dict[str, Any] = {
            "callback_query_id": normalized_callback_query_id,
            "show_alert": show_alert,
        }

        if text:
            payload["text"] = text

        result = self._post(
            path="/answerCallbackQuery",
            payload=payload,
        )

        if not isinstance(result, bool):
            raise TelegramApiError(
                "Telegram answerCallbackQuery result must be boolean"
            )

        return result

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _get(
        self,
        *,
        path: str,
        params: Mapping[str, Any],
    ) -> Any:
        response = self.client.get(
            path,
            params=dict(params),
        )
        return self._extract_result(
            response=response,
            method_name=path.lstrip("/"),
        )

    def _post(
        self,
        *,
        path: str,
        payload: Mapping[str, Any],
    ) -> Any:
        response = self.client.post(
            path,
            json=dict(payload),
        )
        return self._extract_result(
            response=response,
            method_name=path.lstrip("/"),
        )

    def _extract_result(
        self,
        *,
        response: httpx.Response,
        method_name: str,
    ) -> Any:
        response.raise_for_status()

        try:
            data = response.json()
        except ValueError as exc:
            raise TelegramApiError(
                f"Telegram {method_name} returned invalid JSON"
            ) from exc

        if not isinstance(data, dict):
            raise TelegramApiError(
                f"Telegram {method_name} response must be an object"
            )

        if not data.get("ok"):
            description = data.get("description") or data
            raise TelegramApiError(
                f"Telegram {method_name} failed: {description}"
            )

        return data.get("result")

    def _ensure_object_result(
        self,
        *,
        method_name: str,
        result: Any,
    ) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise TelegramApiError(
                f"Telegram {method_name} result must be an object"
            )

        return result

    def _normalize_chat_id(
        self,
        chat_id: int | str,
    ) -> int | str:
        if isinstance(chat_id, int):
            return chat_id

        normalized_chat_id = str(chat_id).strip()

        if not normalized_chat_id:
            raise TelegramApiError("Telegram chat_id is empty")

        if normalized_chat_id.lstrip("-").isdigit():
            return int(normalized_chat_id)

        return normalized_chat_id
