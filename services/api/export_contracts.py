"""Export compatible C1 and additive C2 contracts; --check fails on schema drift."""

import argparse
import json

from .app import ROOT, create_app
from .agent.schemas import AgentIntent
from .agent.dag.schemas import GraphDefinition
from .agent.orchestration.schemas import ExecutionReport


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    schema = AgentIntent.model_json_schema()
    schema['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
    dag_schema = GraphDefinition.model_json_schema()
    dag_schema['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
    report_schema=ExecutionReport.model_json_schema()
    report_schema['$schema']='https://json-schema.org/draft/2020-12/schema'
    outputs = {'agent-intent.c1.schema.json': schema,
               'application-api.c1.openapi.json': create_app(enable_c2=False).openapi(),
               'dag-plan.c2.schema.json': dag_schema,
               'application-api.c2.openapi.json': create_app(enable_c3=False).openapi(),
               'agent-report.c3.schema.json':report_schema,
               'application-api.c3.openapi.json':create_app(enable_c4=False).openapi(),
               'application-api.c4.openapi.json':create_app().openapi()}
    for name, data in outputs.items():
        path = ROOT / 'contracts' / name
        contents = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
        if args.check:
            if not path.is_file() or path.read_text(encoding='utf-8') != contents:
                raise SystemExit(f'C1 contract drift: {path.name}; rerun services.api.export_contracts')
        else:
            path.write_text(contents, encoding='utf-8')
        print(('checked ' if args.check else 'wrote ') + name)


if __name__ == '__main__':
    main()
