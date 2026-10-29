"""Explicit offline development factory; no hardcoded credentials or bank execution."""

import os

from demo.provider import ChatProvider

from .app import ROOT, create_app
from .agent.adapters import FixtureAnalysis
from .agent.auth import DevelopmentTokens, Principal


def create_dev_app():
    token = os.getenv('FUTURELEDGER_DEV_TOKEN')
    if not token or len(token) < 24:
        raise RuntimeError('Set FUTURELEDGER_DEV_TOKEN to a private local token of at least 24 characters.')
    auth = DevelopmentTokens({token: Principal('test_user', ('acc_daily', 'acc_savings'), ('personal_ledger',))})
    provider = ChatProvider()
    return create_app(authenticator=auth, analysis=FixtureAnalysis(ROOT / 'data/benchmark.sqlite'),
                      agent_provider=provider if provider.provider != 'local' else None)
