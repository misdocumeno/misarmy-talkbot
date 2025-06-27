import os
import subprocess

for root, dirs, files in os.walk(os.path.dirname(os.path.realpath(__file__))):
    for file in files:
        if file.endswith('.po'):
            po_path = os.path.join(root, file)
            mo_path = po_path.replace('.po', '.mo')
            subprocess.run(['msgfmt', '-o', mo_path, po_path])
