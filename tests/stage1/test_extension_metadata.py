"""Integrity of the actual Designer XML inputs; no emulation of 1C business logic."""
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2] / 'src/stage-1-prototype'
NS = {'m': 'http://v8.1c.ru/8.3/MDClasses', 'd': 'http://v8.1c.ru/8.3/xcf/dumpinfo'}


class ExtensionMetadata(unittest.TestCase):
    def test_configuration_references_exist_and_match(self):
        config = ET.parse(ROOT / 'Configuration.xml')
        directories = {'InformationRegister': 'InformationRegisters', 'CommonModule': 'CommonModules'}
        for node in config.findall('m:Configuration/m:ChildObjects/*', NS):
            kind = node.tag.split('}')[-1]
            if kind not in directories:
                continue
            with self.subTest(kind=kind, name=node.text):
                obj = ET.parse(ROOT / directories[kind] / (node.text + '.xml'))
                self.assertEqual(obj.find(f'm:{kind}/m:Properties/m:Name', NS).text, node.text)

    def test_dump_ids_match_new_objects_and_fields(self):
        dump = ET.parse(ROOT / 'ConfigDumpInfo.xml')
        index = {node.get('name'): node.get('id') for node in dump.findall('.//d:Metadata', NS)}
        registers = ['Расш1_ЖурналОперацийБ24', 'Расш1_СостояниеЗаказовБ24',
                     'Расш1_УведомленияБ24', 'Расш1_АудитСвязейБ24', 'Расш1_СвязиЗаказовБ24']
        for name in registers:
            obj = ET.parse(ROOT / 'InformationRegisters' / (name + '.xml')).find('m:InformationRegister', NS)
            full = 'InformationRegister.' + name
            self.assertEqual(index[full], obj.get('uuid'))
            for field in obj.findall('m:ChildObjects/*', NS):
                path = full + '.' + field.tag.split('}')[-1] + '.' + field.find('m:Properties/m:Name', NS).text
                with self.subTest(field=path):
                    self.assertEqual(index[path], field.get('uuid'))

    def test_qname_type_prefixes_declared(self):
        for path in ROOT.glob('InformationRegisters/*.xml'):
            prefixes = {prefix for _, (prefix, _) in ET.iterparse(path, events=['start-ns'])}
            for node in ET.parse(path).getroot().iter():
                if node.tag == '{http://v8.1c.ru/8.1/data/core}Type' and ':' in (node.text or ''):
                    with self.subTest(file=path.name, type=node.text):
                        self.assertIn(node.text.split(':')[0], prefixes)


if __name__ == '__main__':
    unittest.main()
