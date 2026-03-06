from Parsers.qiskitparser import QiskitParser
import os

dir_path = "../Attachments/Sample Code/"
parser = QiskitParser()

for file in os.listdir(dir_path):
    if file.endswith(".py"):
        try:
            parser.parse_file(dir_path + file)
            print(f"{file}: {parser.program}")
        except Exception as e:
            print(f"{file}: {e}")
