# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Concat contributors
import importlib.util
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location('packaging', Path(__file__).with_name('package-android-service.py'))
packaging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packaging)
A = packaging.A

class ManifestTests(unittest.TestCase):
    def test_preserves_native_activity_and_adds_nonexported_service_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'AndroidManifest.xml'
            path.write_text('''<manifest xmlns:android="http://schemas.android.com/apk/res/android"
              package="org.standalones.concat" android:versionCode="123">
              <uses-sdk android:minSdkVersion="26" android:targetSdkVersion="35"/>
              <uses-permission android:name="android.permission.INTERNET"/>
              <application android:hasCode="false" android:label="Concat">
                <activity android:name="android.app.NativeActivity" android:configChanges="orientation|uiMode">
                  <meta-data android:name="android.app.lib_name" android:value="concat_android"/>
                </activity>
              </application></manifest>''')
            packaging.manifest_with_service(path, path)
            packaging.manifest_with_service(path, path)
            root = ET.parse(path).getroot()
            self.assertEqual(root.get(A + 'versionCode'), '123')
            app = root.find('application')
            self.assertEqual(app.get(A + 'hasCode'), 'true')
            self.assertEqual(app.find('activity').get(A + 'configChanges'), 'orientation|uiMode')
            self.assertEqual(app.find('activity/meta-data').get(A + 'value'), 'concat_android')
            self.assertEqual(len(app.findall('service')), 1)
            service = app.find('service')
            self.assertEqual(service.get(A + 'exported'), 'false')
            self.assertEqual(service.get(A + 'stopWithTask'), 'false')
            self.assertEqual(service.get(A + 'foregroundServiceType'), 'dataSync|mediaProcessing')
            self.assertEqual(len(root.findall('uses-permission')), 6)

    def test_refuses_another_application(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'manifest.xml'
            path.write_text('<manifest package="other"><application/></manifest>')
            with self.assertRaises(ValueError):
                packaging.manifest_with_service(path, path)

if __name__ == '__main__':
    unittest.main()
