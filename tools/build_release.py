"""Build the Blender install ZIP, source ZIP and SHA256SUMS.txt locally."""
import ast,hashlib,shutil,zipfile,argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def files(root):
    for p in sorted(root.rglob('*')):
        if not p.is_file() or any(x in ('.git','__pycache__','.pytest_cache','dist') for x in p.relative_to(root).parts):continue
        if p.suffix.lower() in ('.pyc','.pyo','.blend','.blend1','.log','.zip'):continue
        yield p
def build(output):
    output.mkdir(parents=True,exist_ok=True)
    for name in ('README.md','CHANGELOG.md','RELEASE.md','LICENSE'):shutil.copy2(ROOT/name,ROOT/'mmd_tools'/name)
    shutil.copytree(ROOT/'docs',ROOT/'mmd_tools/docs',dirs_exist_ok=True)
    tree=ast.parse((ROOT/'mmd_tools/__init__.py').read_text(encoding='utf8'))
    info=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='bl_info' for t in n.targets))
    version='.'.join(map(str,info['version']));base='MMD-Tools-3.6-Continued'
    archives=[]
    for name,folder,prefix in ((base+'-v'+version+'.zip',ROOT/'mmd_tools','mmd_tools'),(base+'-Source-v'+version+'.zip',ROOT,base+'-Source-v'+version)):
        target=output/name
        with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
            for p in files(folder):
                if output==p.parent or output in p.parents:continue
                z.write(p,str(Path(prefix)/p.relative_to(folder)).replace('\\','/'))
        archives.append(target)
    (output/'SHA256SUMS.txt').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in archives),encoding='utf8')
    print('\n'.join(str(p) for p in archives))
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'dist');build(parser.parse_args().output.resolve())
