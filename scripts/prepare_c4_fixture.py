"""Generate only the ignored SQLite fixture; never overwrite published truth."""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def main():
    target=ROOT/'data/benchmark.sqlite'
    if target.is_file():
        print('Using existing ignored SQLite fixture')
        return
    with tempfile.TemporaryDirectory(prefix='futureledger-fixture-') as temp:
        root=Path(temp)
        for name in ('scripts','data','benchmark'):
            shutil.copytree(ROOT/name,root/name,ignore=shutil.ignore_patterns('__pycache__','*.sqlite*'))
        subprocess.run([sys.executable,str(root/'scripts/generate_data.py')],check=True,capture_output=True)
        # Reproduction must agree with checked-in data, rather than replace it.
        for source in (root/'data').iterdir():
            if source.is_file() and source.name!='benchmark.sqlite':
                original=ROOT/'data'/source.name
                if not original.is_file() or source.read_bytes()!=original.read_bytes():
                    raise RuntimeError('Published fixture differs: '+source.name)
        for name in ('analysis_cases.json','cashflow_cases.json'):
            if (root/'benchmark'/name).read_bytes()!=(ROOT/'benchmark'/name).read_bytes():
                raise RuntimeError('Published benchmark differs: '+name)
        shutil.copy2(root/'data/benchmark.sqlite',target)
    print('Prepared SQLite; published data and gold answers unchanged')

if __name__=='__main__': main()
