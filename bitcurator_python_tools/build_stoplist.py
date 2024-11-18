#!/usr/bin/env python3
# coding=UTF-8
# Create a stoplist from bulk_extractor reports

__version__ = "1.4.0"

import argparse
import zlib
import zipfile
from bitcurator_python_tools import bulk_extractor_reader

build_stoplist_version = "1.4"

all_emails = set()


def process(report, fsc):
    b1 = bulk_extractor_reader.BulkReport(report, do_validate=False)
    print("Reading email.txt")
    try:
        for line in b1.open("email.txt"):
            fsc.write(line)
    except KeyError:
        pass

    try:
        h = b1.read_histogram("email_histogram.txt")
        for a in h:
            all_emails.add(a)
    except KeyError:
        pass
    print(f"Processed {report}; now {len(all_emails)} unique emails")


def main():
    parser = argparse.ArgumentParser(
        description="Create a stop list from bulk_extractor reports",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--stoplist", default="stop-list.txt")
    parser.add_argument("--stopcontext", default="stop-context.txt")
    parser.add_argument(
        "reports",
        nargs="+",
        help="BE reports or ZIPfiles with email.txt files to ignore",
    )
    parser.add_argument("-v", "--version", action='version', version=f"%(prog)s {__version__}")
    args = parser.parse_args()

    with open(args.stopcontext, "wb") as fsc:

        for fn in args.reports:
            try:
                process(fn, fsc)
            except zlib.error:
                print(f"{fn} appears corrupt")
            except zipfile.BadZipFile:
                print(f"{fn} is a bad zip file")

        with open(args.stoplist, "wb") as f:
            f.write(b"\n".join(sorted(all_emails)))


if __name__ == "__main__":
    main()
