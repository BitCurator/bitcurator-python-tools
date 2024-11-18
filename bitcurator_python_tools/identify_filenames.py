#!/usr/bin/env python3
"""
Reports where the features are located within an image.

This script relies on the XML output produced by `fiwalk' and the TXT files
produced by `bulk_extractor'.  By combining these two, the script will show
the file on the filesystem where the `bulk_extractor' match was found.

There are two ways to do this: a database of features, which is
examined for every byte run, or a database of byte runs, which is
examined for every feature.
"""

import sys
import bisect
import os
import re
import time
import pickle
from argparse import ArgumentParser

try:
    import dfxml, dfxml.fiwalk as fiwalk

    try:
        if dfxml.__version__ < "1.0.0":
            raise RuntimeError("Requires dfxml.py Version 1.0.0 or above")
    except AttributeError as exc:
        raise RuntimeError("Requires dfxml.py Version 1.0.0 or above") from exc
except ImportError as exc:
    raise ImportError("This script requires the dfxml and fiwalk modules for Python.") from exc

try:
    from bitcurator_python_tools import bulk_extractor_reader
    try:
        if bulk_extractor_reader.__version__ < "1.0.0":
            raise RuntimeError(
                "Requires bulk_extractor_reader.py Version 1.0.0 or above"
            )
    except AttributeError as exc:
        raise RuntimeError("Requires bulk_extractor_reader.py Version 1.0.0 or above") from exc
except ImportError as exc:
    raise ImportError("This script requires bulk_extractor_reader.py") from exc


__version__ = "1.5.0"


class byterundb:
    """The byte run database holds a set of byte runs, sorted by the
    start byte. It can be searched to find the name of a file that
    corresponds to a byte run."""

    def __init__(self, args):
        self.rary = []  # each element is (runstart,runend,(fileinfo))
        self.sorted = True  # whether or not sorted
        self.args = args

    def __iter__(self):
        return self.rary.__iter__()

    def __len__(self):
        return len(self.rary)

    def dump(self):
        for e in self.rary:
            print(e)

    def add_extent(self, offset, length, fileinfo):
        """Add the extent the array, but fix any invalid arguments"""
        if not isinstance(offset, int) or not isinstance(length, int):
            return
        self.rary.append((offset, offset + length, fileinfo))
        self.sorted = False

    def search_offset(self, pos):
        """Return the tuple associated with a offset"""
        if self.sorted is False:
            self.rary.sort()
            self.sorted = True

        p = bisect.bisect_left(self.rary, ((pos, 0, "")))

        # If the offset matches the first byte in the returned byte run,
        # we have found the matching exten
        try:
            if self.rary[p][0] == pos:
                return self.rary[p]
        except IndexError:
            pass

        # If the first element in the array was found, all elements are to the right
        # of the provided offset, so there is no byte extent that maches.

        if p == 0:
            return None

        # Look at the byte extent whose origin is to the left
        # of pos. If the extent includes pos, return it, otherwise
        # return None
        if self.rary[p - 1][0] <= pos < self.rary[p - 1][1]:
            return self.rary[p - 1]

        return None

    def process_fi(self, fi, args):
        """Read an XML file and add each byte run to this database"""
        def gval(x):
            """Always return X as bytes"""
            if x is None:
                return b""
            if isinstance(x, bytes):
                return x
            if not isinstance(x, str):
                x = str(x)
            return x.encode("utf-8")

        for run in fi.byte_runs():
            try:
                fname = gval(fi.filename())
                md5val = gval(fi.md5())
                if not fi.allocated():
                    fname = b"*" + fname
                if args.mactimes:
                    fileinfo = (
                        fname,
                        md5val,
                        gval(fi.crtime()),
                        gval(fi.ctime()),
                        gval(fi.mtime()),
                        gval(fi.atime()),
                    )
                else:
                    fileinfo = (fname, md5val)
                self.add_extent(run.img_offset, run.len, fileinfo)
            except TypeError as e:
                pass


xor_re = re.compile(b"^(\\d+)\\-XOR\\-(\\d+)")


class byterundb2:
    """Maintain two byte run databases, one for allocated files, one for unallocated files."""

    def __init__(self, args):
        self.args = args
        self.allocated = byterundb(args)
        self.unallocated = byterundb(args)
        self.filecount = 0

    def __len__(self):
        return len(self.allocated) + len(self.unallocated)

    def process(self, fi):
        if fi.allocated():
            self.allocated.process_fi(fi, self.args)
        else:
            self.unallocated.process_fi(fi, self.args)
        self.filecount += 1
        if self.filecount % 1000 == 0:
            print(f"Processed {self.filecount} fileobjects in DFXML file")

    def read_xmlfile(self, fname, args):
        print(f"Reading file map from XML file {fname}")
        fiwalk.fiwalk_using_sax(xmlfile=open(fname, "rb"), callback=self.process)

    def read_imagefile(self, fname):
        if args.nohash:
            fiwalk_args = "-z"
        else:
            fiwalk_args = "-zM"
        print(f"Reading file map by running fiwalk on {fname}")
        fiwalk.fiwalk_using_sax(
            imagefile=open(fname, "rb"), callback=self.process, fiwalk_args=fiwalk_args
        )

    def search_offset(self, offset):
        """First search the allocated. If there is nothing, search unallocated"""
        r = self.allocated.search_offset(offset)
        if not r:
            r = self.unallocated.search_offset(offset)
        return r

    def path_to_offset(self, offset):
        """If the path has an XOR transformation, add the offset within
        the XOR to the initial offset. Otherwise don't. Return the integer
        value of the offset."""
        m = xor_re.search(offset)
        if m:
            return int(m.group(1)) + int(m.group(2))
        negloc = offset.find(b"-")
        if negloc == -1:
            return int(offset)
        return int(offset[0:negloc])

    def search_path(self, path):
        return self.search_offset(self.path_to_offset(path))

    def dump(self):
        print("Allocated:")
        self.allocated.dump()
        print("Unallocated:")
        self.unallocated.dump()


def cmd_line():
    "Return the binary value of the command that invoked this program"
    return b" ".join([s.encode("latin1") for s in sys.argv])


def process_featurefile2(rundb, infile, outfile, args):
    """Returns features from infile, determines the file for each, writes results to outfile"""
    # Stats
    unallocated_count = 0
    feature_count = 0
    features_encoded = 0
    located_count = 0

    outfile.write(b"# Position\tFeature")
    if not args.terse:
        outfile.write(b"\tContext")
    outfile.write(b"\tFilename\tMD5")
    if args.mactimes:
        outfile.write(b"\tcrtime\tctime\tmtime\tatime")
    outfile.write(b"\n")
    outfile.write(b"# " + cmd_line() + b"\n")
    t0 = time.time()
    linenumber = 0
    for line in infile:
        linenumber += 1
        if bulk_extractor_reader.is_comment_line(line):
            outfile.write(line)
            continue
        try:
            (path, feature, context) = line[:-1].split(b"\t")
        except ValueError as e:
            print(e)
            print(f"Offending line {linenumber}:", line[:-1])
            continue
        feature_count += 1

        # Increment counter if this feature was encoded
        if b"-" in path:
            features_encoded += 1

        # Search for feature in database
        tpl = rundb.search_path(path)

        # Output to annotated feature file
        outfile.write(path)
        outfile.write(b"\t")
        outfile.write(feature)
        if not args.terse:
            outfile.write(b"\t")
            outfile.write(context)

        # If we found the data, output that
        if tpl:
            located_count += 1
            outfile.write(b"\t")
            outfile.write(b"\t".join(tpl[2]))  # just the file info
        else:
            unallocated_count += 1
        outfile.write(b"\n")

        if args.debug:
            print("path=", path, "tpl=", tpl, "located_count=", located_count)

    t1 = time.time()
    for title in [
        [f"# Total features input: {feature_count}"],
        [f"# Total features located to files: {located_count}"],
        [f"# Total features in unallocated space: {unallocated_count}"],
        [f"# Total features in encoded regions: {features_encoded}"],
        [f"# Total processing time: {(t1 - t0):.1f} seconds"],
    ]:
        outfile.write((f"{title}\n").encode("utf-8"))
    return (feature_count, located_count)


def main():
    parser = ArgumentParser(
        prog="identify_filenames.py",
        description='Identify filenames from "bulk_extractor" output',
    )
    parser.add_argument(
        "bulk_extractor_report",
        action="store",
        help="Directory or ZIP file of bulk_extractor output",
    )
    parser.add_argument(
        "outdir", action="store", help="Output directory; must not exist"
    )
    parser.add_argument("--all", action="store_true", help="Process all feature files")
    parser.add_argument(
        "--featurefiles",
        action="store",
        help="Specific feature file to process; separate with commas",
    )
    parser.add_argument(
        "--image_filename",
        action="store",
        help="Overwrite location of image file from bulk_extractor_report report.xml file",
    )
    parser.add_argument(
        "--xmlfile",
        action="store",
        help="Don't run fiwalk; use the provided XML file instead",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List feature files in bulk_extractor_report and exit",
    )
    parser.add_argument(
        "--nohash",
        action="store_true",
        help="Do not calculate MD5 or SHA1 when running fiwalk",
    )
    parser.add_argument("-t", dest="terse", action="store_true", help="Terse output")
    parser.add_argument(
        "-v",
        action="version",
        version="%(prog)s version " + __version__,
        help="Print Version and exit",
    )
    parser.add_argument("--verbose", action="store_true", help="Verbose mode")
    parser.add_argument("--debug", action="store_true", help="Debug mode")
    parser.add_argument(
        "--noxmlfile",
        action="store_true",
        help="Don't run fiwalk; don't use XML file. Just read the feature files (for testing)",
    )
    parser.add_argument(
        "--mactimes",
        action="store_true",
        help="Include mactimes in annotated feature file",
    )
    parser.add_argument(
        "--path",
        action="store",
        help="Just locate path and exit. Only needs XML file, disk image, or bulk_extractor output",
    )
    parser.add_argument("--save", help="Save runs database in a file")
    parser.add_argument("--load", help="Load runs database in a file")

    args = parser.parse_args()

    if args.xmlfile:
        # Verify that the XML file was NOT generated by bulk_extractor
        r = dfxml.creatorobjects_sax(xmlfile=open(args.xmlfile, "rb"))
        generator_program = r[0].program()
        if generator_program == "BULK_EXTRACTOR":
            raise RuntimeError(
                f"\nERROR: {args.xmlfile} was generated by BULK_EXTRACTOR.\nYou must provide a DFXML file that was generated by fiwalk\n"
            )

    # Start the timer used to calculate the total run time
    t0 = time.time()

    rundb = byterundb2(args)

    def read_filemap(rundb):
        if args.xmlfile:
            rundb.read_xmlfile(args.xmlfile, args)
            if len(rundb) == 0:
                raise RuntimeError(
                    f"\nERROR: No files detected in XML file {args.xmlfile}\n"
                )
            return
        if args.image_filename:
            imagefile = args.image_filename
        else:
            imagefile = bulk_extractor_reader.BulkReport(
                args.bulk_extractor_report
            ).image_filename()
        rundb.read_imagefile(imagefile)
        if len(rundb) == 0:
            raise RuntimeError(
                f"\nERROR: No files detected in image file {imagefile}\n"
            )

    if args.path:
        read_filemap(rundb)
        print(f"Locating {args.path}: ")
        res = rundb.search_path(args.path.encode("utf-8"))
        if res:
            print(
                f"Start:     {res[0]}\nLength:    {res[1]}\nFile Name: {res[2]}\nFile MD5:  {res[3]}"
            )
        else:
            print("NOT FOUND")
        sys.exit(0)

    # Open the report
    try:
        report = bulk_extractor_reader.BulkReport(args.bulk_extractor_report)
    except UnicodeDecodeError:
        print(
            f"{args.bulk_extractor_report}/report.xml file contains invalid XML. Cannot continue\n"
        )
        sys.exit(1)

    if args.list:
        print(f"Feature files in {args.bulk_extractor_report}:")
        for fn in report.feature_files():
            print(fn)
        sys.exit(1)

    # Make sure that the user has specified feature files
    if not args.featurefiles and not args.all:
        raise RuntimeError(
            "Please request a specific feature file or --all feature files"
        )

    # Read the file map
    if args.noxmlfile:
        print("TESTING --- will not read XML File")
    elif args.load:
        with open(args.load, "rb") as f:
            rundb = pickle.load(f)
        print(f"Runs database loaded from {args.load}")
    else:
        read_filemap(rundb)

    if args.save:
        with open(args.save, "wb") as f:
            pickle.dump(rundb, f)
        print(f"Runs database saved in {args.save}")

    # Make the output directory if needed
    if not os.path.exists(args.outdir):
        os.mkdir(args.outdir)

    if not os.path.isdir(args.outdir):
        raise RuntimeError(f"{args.outdir} must be a directory")

    # Process each feature file
    feature_file_list = None
    if args.featurefiles:
        feature_file_list = args.featurefiles.split(",")
    if args.all:
        feature_file_list = report.feature_files()
        try:
            feature_file_list.remove("tcp.txt")  # not needed
        except ValueError:
            pass

    total_features = 0
    total_located = 0
    for feature_file in feature_file_list:
        output_fn = os.path.join(args.outdir, (f"annotated_{feature_file}"))
        if os.path.exists(output_fn):
            raise RuntimeError(f"{output_fn} exists")
        print(f"feature_file: {feature_file}")
        (feature_count, located_count) = process_featurefile2(
            rundb, report.open(feature_file, mode="rb"), open(output_fn, "wb"), args
        )
        total_features += feature_count
        total_located += located_count
    print("******************************")
    print(f"** Total Features: {total_features:8} **")
    print(f"** Total Located:  {total_located:8} **")
    print("******************************")


if __name__ == "__main__":
    main()
