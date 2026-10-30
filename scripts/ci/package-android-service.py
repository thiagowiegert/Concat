# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Concat contributors
"""Finish cargo-apk's native APK with framework-loadable Java and an FGS.

Run after cargo apk build. Keeps the generated manifest (including version,
activity flags, features and library metadata), native libraries and assets.
Requires Android SDK platform/build-tools 35 and a signing keystore.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile

ANDROID = 'http://schemas.android.com/apk/res/android'
ET.register_namespace('android', ANDROID)
A = '{' + ANDROID + '}'
PERMISSIONS = ('FOREGROUND_SERVICE', 'FOREGROUND_SERVICE_MEDIA_PROCESSING',
               'FOREGROUND_SERVICE_DATA_SYNC', 'POST_NOTIFICATIONS', 'WAKE_LOCK')


def manifest_with_service(source, destination):
    tree = ET.parse(source)
    root = tree.getroot()
    app = root.find('application')
    if app is None or root.attrib.get('package') != 'org.standalones.concat':
        raise ValueError('Not the Concat manifest')
    app.set(A + 'hasCode', 'true')
    existing = {p.get(A + 'name') for p in root.findall('uses-permission')}
    for permission in PERMISSIONS:
        name = 'android.permission.' + permission
        if name not in existing:
            ET.SubElement(root, 'uses-permission', {A + 'name': name})
    name = 'org.standalones.concat.ConcatExportService'
    if not any(s.get(A + 'name') == name for s in app.findall('service')):
        ET.SubElement(app, 'service', {
            A + 'name': name, A + 'exported': 'false', A + 'stopWithTask': 'false',
            A + 'foregroundServiceType': 'dataSync|mediaProcessing',
        })
    tree.write(destination, encoding='utf-8', xml_declaration=True)


def run(*args):
    subprocess.run([str(a) for a in args], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('apk', type=Path)
    args = parser.parse_args()
    apk = args.apk.resolve()
    repo = Path(__file__).resolve().parents[2]
    crate = repo / 'src/crates/concat-android'
    sdk = Path(os.environ.get('ANDROID_HOME') or os.environ['ANDROID_SDK_ROOT'])
    build = sdk / 'build-tools/35.0.0'
    jar = sdk / 'platforms/android-35/android.jar'
    key = os.environ['CARGO_APK_RELEASE_KEYSTORE']
    # cargo-apk leaves its source manifest beside the APK, sometimes in a crate subfolder.
    manifests = [p for p in apk.parent.rglob('AndroidManifest.xml')
                 if ET.parse(p).getroot().attrib.get('package') == 'org.standalones.concat']
    if len(manifests) != 1:
        raise RuntimeError(f'Expected one generated manifest, found {manifests}')
    with tempfile.TemporaryDirectory(prefix='concat-apk-') as tmp:
        tmp = Path(tmp)
        manifest = tmp / 'AndroidManifest.xml'
        manifest_with_service(manifests[0], manifest)
        classes = tmp / 'classes'
        classes.mkdir()
        run(Path(os.environ['JAVA_HOME']) / 'bin/javac', '-encoding', 'UTF-8',
            '-source', '8', '-target', '8', '-classpath', jar, '-d', classes,
            *sorted((crate / 'java').glob('*.java')))
        run(build / 'd8', '--lib', jar, '--min-api', '26', '--output', tmp,
            *sorted(classes.rglob('*.class')))
        unsigned = tmp / 'unsigned.apk'
        run(build / 'aapt', 'package', '-f', '-M', manifest, '-S', crate / 'res',
            '-I', jar, '-F', unsigned)
        with zipfile.ZipFile(apk) as old, zipfile.ZipFile(unsigned, 'a') as new:
            for info in old.infolist():
                if info.filename.startswith(('lib/', 'assets/')):
                    new.writestr(info, old.read(info.filename))
            new.write(tmp / 'classes.dex', 'classes.dex')
        aligned = tmp / 'aligned.apk'
        run(build / 'zipalign', '-P', '16', '-f', '4', unsigned, aligned)
        run(build / 'apksigner', 'sign', '--ks', key, '--ks-pass',
            'env:CARGO_APK_RELEASE_KEYSTORE_PASSWORD', aligned)
        run(build / 'apksigner', 'verify', '--verbose', aligned)
        apk.write_bytes(aligned.read_bytes())
    print(f'Packaged foreground export service: {apk}')


if __name__ == '__main__':
    main()

