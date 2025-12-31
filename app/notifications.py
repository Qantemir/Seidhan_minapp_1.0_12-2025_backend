"""Утилиты для отправки уведомлений администраторам через Telegram Bot API."""

import asyncio
import json
import logging
from pathlib import Path

import httpx
from bson import ObjectId
from gridfs import GridFS
from motor.motor_asyncio import AsyncIOMotorDatabase

from .config import get_settings
from .utils import get_gridfs

logger = logging.getLogger(__name__)


def format_amount(amount: float) -> str:
    """
    Форматирует сумму, убирая .00 для целых чисел.

    Args:
        amount: Сумма для форматирования

    Returns:
        Отформатированная строка суммы
    """
    if amount == int(amount):
        return str(int(amount))
    return f"{amount:.2f}".rstrip("0").rstrip(".")


async def notify_admins_new_order(
    order_id: str,
    customer_name: str,
    customer_phone: str,
    delivery_address: str,
    total_amount: float,
    items: list,
    user_id: int,
    receipt_file_id: str | None,
    db: AsyncIOMotorDatabase,
) -> None:
    """
    Отправляет простое уведомление всем администраторам о новом заказе.

    Args:
        order_id: ID заказа
        customer_name: Имя клиента
        customer_phone: Телефон клиента
        delivery_address: Адрес доставки
        total_amount: Общая сумма заказа
        items: Список товаров в заказе
        user_id: Telegram ID клиента
        receipt_file_id: ID файла чека в GridFS (может быть None)
        db: База данных для доступа к GridFS
    """
    settings = get_settings()

    # Быстрая проверка настроек
    if not settings.telegram_bot_token:
        return
    
    if not settings.admin_ids:
        return

    # Простое сообщение без деталей
    message = f"🆕 *Новый заказ!*\n\n📋 Заказ: `{order_id[-6:]}`"

    # Отправляем уведомление каждому администратору без кнопок
    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = []
        for admin_id in settings.admin_ids:
            tasks.append(
                _send_simple_notification(
                    client,
                    settings.telegram_bot_token,
                    admin_id,
                    message,
                    None,  # Без кнопок
                )
            )

        # Выполняем все отправки параллельно
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Логируем только ошибки
        success_count = sum(1 for r in results if r is True)
        failed_count = len(results) - success_count
        if failed_count > 0:
            logger.error(f"Не удалось отправить уведомление о новом заказе {order_id} {failed_count} админам")


async def _send_simple_notification(
    client: httpx.AsyncClient,
    bot_token: str,
    admin_id: int,
    message: str,
    keyboard: dict | None,
) -> bool:
    """
    Отправляет простое текстовое уведомление администратору.

    Returns:
        True если отправка успешна, False в противном случае
    """
    try:
        api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": admin_id,
            "text": message,
            "parse_mode": "Markdown",
        }
        if keyboard:
            payload["reply_markup"] = keyboard
        
        response = await client.post(api_url, json=payload)
        return response.json().get("ok", False)
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления администратору {admin_id}: {e}")
        return False


async def notify_admin_order_accepted(
    order_id: str,
    customer_name: str,
    customer_phone: str,
    delivery_address: str,
    total_amount: float,
    items: list,
    user_id: int,
    receipt_file_id: str | None,
    delivery_time_slot: str,
    db: AsyncIOMotorDatabase,
) -> None:
    """
    Отправляет полное уведомление администратору о принятом заказе с временным промежутком,
    всей информацией, товарами и чеком.

    Args:
        order_id: ID заказа
        customer_name: Имя клиента
        customer_phone: Телефон клиента
        delivery_address: Адрес доставки
        total_amount: Общая сумма заказа
        items: Список товаров в заказе
        user_id: Telegram ID клиента
        receipt_file_id: ID файла чека в GridFS
        delivery_time_slot: Временной промежуток доставки (например, "13:00-14:00")
        db: База данных для доступа к GridFS
    """
    settings = get_settings()

    # Быстрая проверка настроек
    if not settings.telegram_bot_token:
        return
    
    if not settings.admin_ids:
        return

    # Получаем информацию о товарах с вкусами из базы данных
    items_details = []
    for item in items:
        product_id = item.get("product_id")
        variant_id = item.get("variant_id")
        quantity = item.get("quantity", 1)
        product_name = item.get("product_name", "Товар")
        variant_name = item.get("variant_name")

        # Оптимизированная загрузка variant_name (только если нужно)
        if not variant_name and variant_id and product_id:
            try:
                from .utils import as_object_id

                product = await db.products.find_one({"_id": as_object_id(product_id)}, {"variants": 1, "name": 1})
                if product:
                    variant = next((v for v in product.get("variants", []) if v.get("id") == variant_id), None)
                    if variant:
                        variant_name = variant.get("name", "")
                    if not product_name:
                        product_name = product.get("name", "Товар")
            except Exception:
                pass  # Игнорируем ошибки для скорости

        items_details.append({"product_name": product_name, "variant_name": variant_name or "", "quantity": quantity})

    # Формируем раскрытый список товаров
    items_text = "📦 *Товары:*\n"
    for idx, item_detail in enumerate(items_details, 1):
        variant_info = f" ({item_detail['variant_name']})" if item_detail["variant_name"] else ""
        items_text += f"{idx}. {item_detail['product_name']}{variant_info} × {item_detail['quantity']}\n"

    # Формируем ссылку на 2ГИС для адреса
    from urllib.parse import quote

    # Кодируем оригинальный адрес со всеми символами включая "/"
    address_encoded = quote(delivery_address, safe="")
    address_2gis_url = f"https://2gis.kz/search/{address_encoded}"
    address_link = f"[{delivery_address}]({address_2gis_url})"

    # Формируем текст сообщения
    items_total = sum((item.get("price", 0) or 0) * (item.get("quantity", 0) or 0) for item in items)
    # Вычисляем delivery_fee как разницу между total_amount и items_total
    delivery_fee = total_amount - items_total
    message = (
        f"✅ *Заказ принят!*\n\n"
        f"📋 Заказ: `{order_id[-6:]}`\n"
        f"⏰ Время доставки: *{delivery_time_slot}*\n\n"
        f"👤 Клиент: {customer_name}\n"
        f"📞 Телефон: {customer_phone}\n"
        f"📍 Адрес: {address_link}\n"
        f"💰 Товары: {format_amount(items_total)} ₸\n"
        f"🚚 Доставка: {format_amount(delivery_fee)} ₸\n"
        f"💰 *Итого: {format_amount(total_amount)} ₸*\n\n"
        f"{items_text}"
    )

    # Получаем файл чека из GridFS
    receipt_data = None
    receipt_filename = None
    receipt_content_type = None
    if receipt_file_id:
        try:
            fs = get_gridfs()
            loop = asyncio.get_event_loop()
            grid_file = await loop.run_in_executor(None, lambda: fs.get(ObjectId(receipt_file_id)))
            receipt_data = await loop.run_in_executor(None, grid_file.read)
            receipt_filename = grid_file.filename or "receipt"
            receipt_content_type = grid_file.content_type or "application/octet-stream"
            if not receipt_data:
                receipt_data = None
        except Exception:
            receipt_data = None

    # Отправляем уведомление каждому администратору
    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = []
        for admin_id in settings.admin_ids:
            tasks.append(
                _send_notification_with_receipt(
                    client,
                    settings.telegram_bot_token,
                    admin_id,
                    message,
                    receipt_data,
                    receipt_filename,
                    receipt_content_type,
                )
            )

        # Выполняем все отправки параллельно
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Логируем только ошибки
        success_count = sum(1 for r in results if r is True)
        failed_count = len(results) - success_count
        if failed_count > 0:
            logger.error(f"Не удалось отправить полное уведомление о принятом заказе {order_id} {failed_count} админам")


async def _send_notification_with_receipt(
    client: httpx.AsyncClient,
    bot_token: str,
    admin_id: int,
    message: str,
    receipt_data: bytes | None,
    receipt_filename: str | None,
    receipt_content_type: str | None,
) -> bool:
    """
    Отправляет уведомление администратору с фото чека.

    Returns:
        True если отправка успешна, False в противном случае
    """
    try:
        file_sent = False

        # Сначала отправляем фото/документ чека, если он есть
        if receipt_data and receipt_filename:
            # Определяем тип файла по расширению или content_type
            file_extension = Path(receipt_filename).suffix.lower()
            is_image = file_extension in {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"} or (
                receipt_content_type and receipt_content_type.startswith("image/")
            )
            is_pdf = file_extension == ".pdf" or receipt_content_type == "application/pdf"

            if is_image:
                api_method = "sendPhoto"
                file_field = "photo"
            elif is_pdf:
                api_method = "sendDocument"
                file_field = "document"
            else:
                api_method = "sendDocument"
                file_field = "document"

            api_url = f"https://api.telegram.org/bot{bot_token}/{api_method}"

            # Отправляем файл с подписью (без кнопок)
            file_tuple = (receipt_filename or "receipt", receipt_data)
            if receipt_content_type:
                file_tuple = (receipt_filename or "receipt", receipt_data, receipt_content_type)

            files = {file_field: file_tuple}
            data = {
                "chat_id": str(admin_id),
                "caption": message,
                "parse_mode": "Markdown",
            }

            try:
                response = await client.post(api_url, data=data, files=files, timeout=30.0)
                response.raise_for_status()  # Вызовет исключение для HTTP ошибок
                result = response.json()

                if result.get("ok"):
                    file_sent = True
                    return True
                else:
                    error_desc = result.get("description", "Unknown error")
                    file_sent = False
            except httpx.HTTPStatusError as e:
                logger.exception(f"HTTP ошибка при отправке файла администратору {admin_id}: {e.response.status_code} - {e.response.text}", exc_info=e)
                file_sent = False
            except Exception as e:
                logger.exception(f"Исключение при отправке файла администратору {admin_id}", exc_info=e)
                file_sent = False

        # Отправляем текстовое сообщение (если файл не отправился или его нет)
        if not file_sent:
            api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {
                "chat_id": admin_id,
                "text": message,
                "parse_mode": "Markdown",
            }
            
            response = await client.post(api_url, json=payload)
            response.raise_for_status()  # Вызовет исключение для HTTP ошибок
            result = response.json()
            if not result.get("ok"):
                return False

        return True
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления администратору {admin_id}: {e}")
        return False


async def notify_customer_order_status(
    user_id: int,
    order_id: str,
    order_status: str,
    customer_name: str | None = None,
    rejection_reason: str | None = None,
    delivery_time_slot: str | None = None,
    db: AsyncIOMotorDatabase | None = None,
) -> None:
    """
    Отправляет уведомление клиенту об изменении статуса заказа.

    Args:
        user_id: Telegram ID клиента
        order_id: ID заказа
        order_status: Новый статус заказа
        customer_name: Имя клиента (опционально, для персонализации)
        rejection_reason: Причина отказа (если статус "отказано")
        delivery_time_slot: Временной промежуток доставки (например, "13:00-14:00")
    """
    settings = get_settings()

    if not settings.telegram_bot_token:
        return

    # Формируем сообщение в зависимости от статуса
    if order_status == "новый":
        status_message = "✅ Ваш заказ получен. Вы получите уведомление о времени доставки."
    elif order_status == "принят":
        if delivery_time_slot:
            status_message = f"✅ Ваш заказ принят! Доставка будет осуществлена в период *{delivery_time_slot}*."
        else:
            status_message = "✅ Ваш заказ принят!"
    elif order_status == "отказано":
        reason_text = f"\n\nПричина: {rejection_reason}" if rejection_reason else ""
        status_message = f"❌ Ваш заказ отклонен по какой-то причине.{reason_text}"
    else:
        status_message = f"Статус вашего заказа изменён: {order_status}"

    # Формируем полное сообщение
    message = f"{status_message}\n\n📋 Заказ: `{order_id[-6:]}`\n📊 Статус: *{order_status}*"

    # Отправляем уведомление клиенту
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            api_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
            response = await client.post(
                api_url,
                json={
                    "chat_id": user_id,
                    "text": message,
                    "parse_mode": "Markdown",
                },
            )
            result = response.json()
            if not result.get("ok"):
                error_code = result.get("error_code")
                error_description = result.get("description", "Unknown error")
                
                # Определяем тип ошибки для более информативного логирования
                error_description_lower = error_description.lower()
                is_invalid_user = any(phrase in error_description_lower for phrase in [
                    "chat not found", "user not found", "receiver not found",
                    "chat_id is empty", "peer_id_invalid"
                ])
                is_blocked = any(phrase in error_description_lower for phrase in [
                    "blocked", "bot blocked", "bot was blocked", "user is deactivated"
                ])
                
                if is_invalid_user:
                    error_type = "невалидный пользователь"
                elif is_blocked:
                    error_type = "пользователь заблокировал бота"
                elif error_code == 429:
                    error_type = "rate limit (слишком много запросов)"
                elif error_code == 400:
                    error_type = "неверный запрос"
                elif error_code == 403:
                    error_type = "доступ запрещен"
                else:
                    error_type = "неизвестная ошибка"
                
                logger.warning(
                    f"Ошибка при отправке уведомления клиенту ({error_type}): {error_description}, "
                    f"error_code={error_code}, user_id={user_id}, order_id={order_id}, "
                    f"status={order_status}"
                )
                
                # Удаляем невалидных пользователей из базы (если пользователь заблокировал бота или невалиден)
                if db and (is_invalid_user or is_blocked):
                    try:
                        result = await db.customers.delete_one({"telegram_id": user_id})
                        if result.deleted_count > 0:
                            logger.info(
                                f"Удален невалидный пользователь из базы: user_id={user_id}, "
                                f"reason={error_type}, order_id={order_id}"
                            )
                    except Exception as e:
                        logger.error(
                            f"Ошибка при удалении невалидного пользователя из базы: user_id={user_id}, "
                            f"order_id={order_id}, error={e}"
                        )
    except httpx.TimeoutException as e:
        logger.error(
            f"Таймаут при отправке уведомления клиенту: user_id={user_id}, order_id={order_id}, "
            f"status={order_status}, timeout=10.0s",
            exc_info=True
        )
    except httpx.HTTPStatusError as e:
        logger.error(
            f"HTTP ошибка при отправке уведомления клиенту: status_code={e.response.status_code}, "
            f"user_id={user_id}, order_id={order_id}, status={order_status}, "
            f"response_text={e.response.text[:200]}",
            exc_info=True
        )
    except Exception as e:
        logger.error(
            f"Исключение при отправке уведомления клиенту: {type(e).__name__}: {str(e)}, "
            f"user_id={user_id}, order_id={order_id}, status={order_status}",
            exc_info=True
        )
