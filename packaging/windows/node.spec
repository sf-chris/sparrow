from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata
root=Path(SPECPATH).parent.parent
payload=root/'packaging'/'build'
datas=[(str(payload/'media'/'bin'),'bin'),(str(payload/'models'/'whisper-base'),'models/whisper-base'),(str(payload/'media'/'sources'),'sources')]
binaries=[];hidden=[]
for package in ['ffsubsync','faster_whisper','ctranslate2','av','onnxruntime','tokenizers','pysubs2','scipy','webrtcvad']:
    data,binary,imports=collect_all(package);datas+=data;binaries+=binary;hidden+=imports
for distribution in ['ffsubsync','faster-whisper','srt','pysubs2']:datas+=copy_metadata(distribution)
a=Analysis([str(root/'packaging/windows/entry.py')],pathex=[str(root)],binaries=binaries,datas=datas,hiddenimports=hidden,
    excludes=['torch','tensorflow','matplotlib','IPython','pytest'],noarchive=False)
pyz=PYZ(a.pure)
cli=EXE(pyz,a.scripts,[],exclude_binaries=True,name='SparrowNode',console=True)
gui=EXE(pyz,a.scripts,[],exclude_binaries=True,name='SparrowNodeSetup',console=False,uac_admin=True)
coll=COLLECT(cli,gui,a.binaries,a.datas,name='SparrowNode')
