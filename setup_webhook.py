#!/usr/bin/env python3
"""
Скрипт для настройки webhook напрямую на бэкенд.

Использование:
    python setup_webhook.py --backend-url https://your-backend.up.railway.app
    python setup_webhook.py --backend-url https://your-backend.up.railway.app --bot-token YOUR_BOT_TOKEN
"""

import argparse
import sys
import httpx
import json


def setup_webhook(backend_url: str, bot_token: str | None = None) -> bool:
    """
    Настраивает webhook через API бэкенда.
    
    Args:
        backend_url: URL бэкенда (без /api в конце)
        bot_token: Опциональный токен бота для прямой настройки через Telegram API
    
    Returns:
        True если успешно, False иначе
    """
    # Убираем /api если есть
    backend_url = backend_url.rstrip('/').replace('/api', '')
    
    if bot_token:
        # Прямая настройка через Telegram API
        webhook_url = f"{backend_url}/api/bot/webhook"
        print(f"🔧 Настраиваем webhook через Telegram API...")
        print(f"   Webhook URL: {webhook_url}")
        
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    f"https://api.telegram.org/bot{bot_token}/setWebhook",
                    json={
                        "url": webhook_url,
                        "allowed_updates": ["callback_query", "message"]
                    }
                )
                result = response.json()
                
                if result.get("ok"):
                    print("✅ Webhook успешно настроен!")
                    return True
                else:
                    error_msg = result.get("description", "Unknown error")
                    print(f"❌ Ошибка: {error_msg}")
                    return False
        except Exception as e:
            print(f"❌ Ошибка при настройке: {e}")
            return False
    else:
        # Настройка через API бэкенда
        setup_url = f"{backend_url}/api/bot/webhook/setup"
        print(f"🔧 Настраиваем webhook через API бэкенда...")
        print(f"   Backend URL: {backend_url}")
        print(f"   Setup endpoint: {setup_url}")
        
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    setup_url,
                    json={"url": backend_url},
                    headers={"Content-Type": "application/json"}
                )
                
                if response.status_code == 200:
                    result = response.json()
                    if result.get("success"):
                        print("✅ Webhook успешно настроен!")
                        print(f"   URL: {result.get('url')}")
                        return True
                    else:
                        print(f"❌ Ошибка: {result.get('detail', 'Unknown error')}")
                        return False
                else:
                    error_text = response.text
                    print(f"❌ Ошибка HTTP {response.status_code}: {error_text}")
                    return False
        except httpx.ConnectError:
            print(f"❌ Не удалось подключиться к {backend_url}")
            print("   Проверьте, что бэкенд доступен из интернета")
            return False
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            return False


def check_webhook_status(backend_url: str) -> None:
    """Проверяет текущий статус webhook."""
    backend_url = backend_url.rstrip('/').replace('/api', '')
    status_url = f"{backend_url}/api/bot/webhook/status"
    
    print(f"\n📊 Проверяем статус webhook...")
    print(f"   Status endpoint: {status_url}")
    
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(status_url)
            
            if response.status_code == 200:
                result = response.json()
                print("\n📋 Текущий статус webhook:")
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                print(f"❌ Ошибка HTTP {response.status_code}: {response.text}")
    except Exception as e:
        print(f"❌ Ошибка при проверке статуса: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Настройка webhook для Telegram Bot API"
    )
    parser.add_argument(
        "--backend-url",
        required=True,
        help="URL бэкенда (например: https://your-backend.up.railway.app)"
    )
    parser.add_argument(
        "--bot-token",
        help="Токен Telegram бота (для прямой настройки через Telegram API)"
    )
    parser.add_argument(
        "--check-status",
        action="store_true",
        help="Проверить текущий статус webhook после настройки"
    )
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("🤖 Настройка Webhook для Telegram Bot")
    print("=" * 60)
    
    success = setup_webhook(args.backend_url, args.bot_token)
    
    if args.check_status or success:
        check_webhook_status(args.backend_url)
    
    if success:
        print("\n✅ Готово! Webhook настроен и готов к работе.")
        sys.exit(0)
    else:
        print("\n❌ Не удалось настроить webhook. Проверьте ошибки выше.")
        sys.exit(1)


if __name__ == "__main__":
    main()

