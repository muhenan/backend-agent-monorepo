from typing import Any


class DomainError(Exception):
    # 建立可公开返回的错误；status_code 为 HTTP 状态，code 为稳定错误码，message 为公开提示，
    # retryable 表示可否重试，document_id 可定位失败文档；self 为当前错误，无返回值。
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        retryable: bool = False,
        document_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.document_id = document_id

    # 转成 HTTP detail 或 SSE error；self 为当前错误，返回不含内部异常信息的字典。
    def to_detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.document_id is not None:
            detail["document_id"] = self.document_id
        return detail
