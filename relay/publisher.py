"""Adapter hàng đợi, đối xứng với worker/queue_adapter.py. Chọn bằng QUEUE_PROVIDER giống worker:
    rabbitmq (mặc định) | azure (Azure Service Bus)
Thêm nền tảng khác (SQS, Pub/Sub) = viết lớp mới cùng giao diện:
    connect(), closed, publish(message_id, payload), wait(seconds), close()

Relay publish vào ĐÚNG queue mà worker nghe (QUEUE_NAME, mặc định `doc2pdf.jobs`). Với RabbitMQ, relay khai
báo queue y hệt worker (cùng tham số dead-letter) để hai bên không xung đột.
"""
import json
import os
import time

import pika
from pika.exceptions import UnroutableError

QUEUE = os.getenv("QUEUE_NAME", "doc2pdf.jobs")
DLQ = f"{QUEUE}.dlq"
SUPPORTED_TYPES = {"job.convert.requested"}


class PermanentPublishError(Exception):
    """Sự kiện không thể gửi dù thử lại (type lạ, không định tuyến được)."""


class RabbitPublisher:
    def __init__(self, url: str) -> None:
        self.url = url
        self.conn = None
        self.ch = None

    @property
    def closed(self) -> bool:
        return self.conn is None or self.conn.is_closed

    def connect(self) -> None:
        self.conn = pika.BlockingConnection(pika.URLParameters(self.url))
        self.ch = self.conn.channel()
        self.ch.confirm_delivery()
        self._declare_topology()

    def _declare_topology(self) -> None:
        # Phải trùng tham số với worker, nếu không RabbitMQ báo PRECONDITION_FAILED
        self.ch.queue_declare(queue=DLQ, durable=True)
        self.ch.queue_declare(queue=QUEUE, durable=True, arguments={
            "x-dead-letter-exchange": "", "x-dead-letter-routing-key": DLQ})

    def publish(self, message_id: str, payload: dict) -> None:
        if payload.get("type") not in SUPPORTED_TYPES:
            raise PermanentPublishError(f"unknown event type: {payload.get('type')}")
        props = pika.BasicProperties(
            message_id=message_id, content_type="application/json", delivery_mode=2,
            type=payload["type"],
            headers={"x-schema-version": payload.get("schema_version", 1),
                     "x-request-id": payload.get("request_id")})
        try:  # default exchange: routing key = tên queue; confirm lỗi/nack sẽ ném AMQPError
            self.ch.basic_publish("", QUEUE, json.dumps(payload).encode(), props, mandatory=True)
        except UnroutableError as e:
            raise PermanentPublishError(f"unroutable: {e}") from e

    def wait(self, seconds: float) -> None:
        self.conn.process_data_events(time_limit=seconds)  # nghỉ nhưng vẫn giữ heartbeat

    def close(self) -> None:
        try:
            if self.conn is not None and self.conn.is_open:
                self.conn.close()
        except Exception:
            pass
        self.conn = self.ch = None


class ServiceBusPublisher:
    """Azure Service Bus. Dead-letter do Service Bus tự xử lý (số lần giao tối đa của queue)."""

    def __init__(self, conn_str: str) -> None:
        self.conn_str = conn_str
        self.client = None
        self.sender = None

    @property
    def closed(self) -> bool:
        return self.sender is None

    def connect(self) -> None:
        from azure.servicebus import ServiceBusClient
        self.client = ServiceBusClient.from_connection_string(conn_str=self.conn_str)
        self.sender = self.client.get_queue_sender(queue_name=QUEUE)

    def publish(self, message_id: str, payload: dict) -> None:
        if payload.get("type") not in SUPPORTED_TYPES:
            raise PermanentPublishError(f"unknown event type: {payload.get('type')}")
        from azure.servicebus import ServiceBusMessage
        msg = ServiceBusMessage(json.dumps(payload), message_id=message_id,
                                content_type="application/json", subject=payload["type"],
                                application_properties={"x-request-id": payload.get("request_id") or ""})
        self.sender.send_messages(msg)  # trả về khi Service Bus đã nhận; lỗi sẽ ném ngoại lệ

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)

    def close(self) -> None:
        for obj in (self.sender, self.client):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        self.sender = self.client = None


def create_publisher():
    provider = os.getenv("QUEUE_PROVIDER", "rabbitmq").strip().lower()
    if provider in ("azure", "servicebus", "azure_servicebus"):
        conn_str = os.getenv("AZURE_SERVICEBUS_CONNECTION_STRING", "")
        if not conn_str:
            raise ValueError("Thiếu biến môi trường AZURE_SERVICEBUS_CONNECTION_STRING")
        return ServiceBusPublisher(conn_str)
    return RabbitPublisher(os.getenv("RABBITMQ_URL", "amqp://doc2pdf:doc2pdf_dev_password@localhost:5672/"))
