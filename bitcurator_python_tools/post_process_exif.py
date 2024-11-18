#!/usr/bin/env python3
#
# postprocess the EXIF output file into a CSV
#

__version__ = "1.3.0"

import xml.parsers.expat
import csv
import sys
import os
import os.path
import sys
import codecs
import argparse

class ExifParser:
    def __init__(self, data):
        self.data = {}
        p = xml.parsers.expat.ParserCreate()
        p.StartElementHandler = self.start_element
        p.CharacterDataHandler = self.char_data
        p.Parse(data)

    def start_element(self, name, attrs):
        self.element = name
        if self.element not in self.data:
            self.data[self.element] = ""

    def char_data(self, data):
        self.data[self.element] += data


def main():
    parser = argparse.ArgumentParser(description="Postprocess EXIF output files into CSV", usage="%(prog)s [options] input.txt output.csv")
    parser.add_argument("infile", help="input file")
    parser.add_argument("outfile", help="output file")
    parser.add_argument("--zap", help="erase outfile", action="store_true")
    parser.add_argument("-v", "--version", action='version', version=f"%(prog)s {__version__}")
    args = parser.parse_args()

    if len(args) != 2:
        parser.print_help()
        parser.exit(1)

    infile = args.infile
    outfile = args.outfile

    if os.path.exists(outfile) and not args.zap:
        raise IOError(f"{outfile} Exists")
        sys.exit(1)

    invalid_tags = 0
    tags = set()
    print(f"Input file: {infile}")
    print(f"Output file: {outfile}")
    out = open(outfile, "w")
    print("Scanning for EXIF tags...")
    for line in open(infile, "r"):
        if ord(line[0:1]) == 65279:
            line = line[1:]
        if line[0:1] == "#":
            continue
        (offset, hash, xmlstr) = line.split("\t")
        try:
            p = ExifParser(xmlstr)
            for tag in p.data.keys():
                tags.add(tag)
        except xml.parsers.expat.ExpatError:
            invalid_tags += 1
            pass
    taglist = list(tags)
    print(f"There are {len(taglist)} exif tags ")
    if invalid_tags:
        print(f"There are {invalid_tags} invalid tags")

    # sort the tags into some sensible order
    def sortfun(a):
        return (".entry_" in a, a)

    taglist.sort(key=sortfun)
    writer = csv.writer(
        open(outfile, "w"), delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL
    )
    writer.writerow(taglist)
    for line in open(infile, "r"):
        if ord(line[0:1]) == 65279:
            line = line[1:]
        if line[0:1] == "#":
            continue
        (offset, hash, xmlstr) = line.split("\t")
        try:
            p = ExifParser(xmlstr)
            writer.writerow([p.data.get(key, "") for key in taglist])
        except xml.parsers.expat.ExpatError:
            print(f"Invalid XML: {xmlstr}")
            pass

if __name__ == "__main__":
    main()
