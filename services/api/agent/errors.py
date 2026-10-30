"""Sanitized errors shared by application and integration boundaries."""


class AgentError(Exception):
    def __init__(self, code: str, status: int = 409, *, recoverable: bool = True,
                 next_action: str = '检查输入或当前状态后重试。'):
        self.code = code
        self.status = status
        self.recoverable = recoverable
        self.next_action = next_action
        super().__init__(code)


def forbidden():
    return AgentError('FORBIDDEN', 403, recoverable=False, next_action='使用当前主体有权访问的会话与资源。')


def unsupported(component: str):
    return AgentError('UNSUPPORTED', 501, recoverable=False,
                      next_action=f'{component}尚未接入；保留提议，不提交银行操作。')
