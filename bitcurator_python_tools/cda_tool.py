#!/usr/bin/env python3
# coding=UTF-8
#
# Cross Drive Analysis tool for bulk extractor.
#
# Features of this program:
# --netmap  -- makes a map of which computers exchanged packets (ethernet maps)
# --makestop  -- Creates a stoplist of features that are on more than a fraction of the drives
# --threshold -- sets the fraction of drives necessary for a feature to be ignored
# --idfeatures  -- spcifies which feature files are used for identity operations
#
# reads multiple bulk_extractor histogram files and outputs:
# stoplist.txt - list of email addresses on more than 1/3 of the disks
# targets.txt  - list of email addresses not on stoplist and the # of drives on which they appear.
#
# Version 1.3 - Complete rewrite; elimiantes driveids and featureids, since strings
#               in Python are hashable (and thus integers). Also uses bulk_extractor_reader


__version__ = "1.4.0"
import os.path
import os
import sys
import collections
import argparse
import glob
from bitcurator_python_tools import bulk_extractor_reader


class Correlator:
    """The main correlator class.
    Correlates features on different disks.
    Python does not automatically uniquify all strings, so we do.

    @property features - a dictionary of all features found. Each value is a dictionary of drives and counts.

    """

    def __init__(self, name):
        self.name = name  # what we are correlating
        self.drives = set()  # the drives that we have seen
        self.features = collections.defaultdict(
            dict
        )  # for each feature, maps to a tupple of (drivename,count)

    def longest_drive_name(self):
        return max(len(s) for s in self.drives)

    def longest_feature_name(self):
        return max(len(s) for s in self.features.keys())

    def ingest_feature_file(self, f, context_stop_list):
        """Read the lines in a feature file; returns how many lines were procesed"""
        drivename = None
        count = 0
        for line in f:
            if isinstance(line, bytes):
                line = line.decode("utf-8")
            m = bulk_extractor_reader.get_property_line(line)
            if m:
                if m[0] == "Filename":
                    drivename = m[1]
                    self.drives.add(drivename)
                    print(f"Scanning {drivename} for {self.name}")
            if bulk_extractor_reader.is_comment_line(line):
                continue
            count += 1
            if context_stop_list is not None:
                (_, feature, context) = line.split("\t")
                context_stop_list.add((feature, context))
                continue
            feature = line.split("\t")[1]
            featuredict = self.features[feature]
            featuredict[drivename] = featuredict.get(drivename, 0) + 1
        print(f"   processed {count} features")
        return count

    def ingest_histogram_file(self, f):
        drivename = None
        for line in f:
            if isinstance(line, bytes):
                line = line.decode("utf-8")
            m = bulk_extractor_reader.get_property_line(line)
            if m:
                if m[0] == "Filename":
                    drivename = m[1]
                    self.drives.add(drivename)
                    print(f"Scanning {drivename} for {self.name}")
                continue
            if bulk_extractor_reader.is_comment_line(line):
                continue
            fields = line.split("\t")
            count = int(fields[0][2:])
            feature = fields[1].strip()
            featuredict = self.features[feature]
            featuredict[drivename] = featuredict.get(drivename, 0) + count

    def dump_stats(self, f):
        f.write(f"Total Drives: {len(self.drives)}\n")
        f.write(f"Distinct {self.name} features: {len(self.features)}\n")
        lfn = int(self.longest_feature_name())
        f.write(f"{'Feature':{lfn}} Count Drives\n")

        def keysortfun(k):
            return (-len(self.features[k]), k)

        for d in sorted(self.features.keys(), key=keysortfun):
            f.write(f"{d:{lfn}} {len(self.features[d])} {self.features[d]}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Cross Drive Analysis with bulk_extractor output"
    )
    parser.add_argument(
        "--netmap",
        help="General GraphViz data for network correlation map",
        action="store_true",
    )
    parser.add_argument(
        "--idcor", help="Perform identity-based correlation", action="store_true"
    )
    parser.add_argument(
        "--makestop",
        help="Make a stop list of identity features on more than THRESHOLD (0..1) drives",
        type=str,
    )
    parser.add_argument(
        "--threshold",
        help="Specify the faction of drives for the threshold",
        type=float,
        default=0.667,
    )
    parser.add_argument(
        "--makecombined",
        help="Combine multiple feature files into a single context stop list with no offests",
        action="store_true",
    )
    parser.add_argument(
        "--idfeatures",
        help="Specifies feature files used for identity operations",
        type=str,
        default="email,ccn,telephone",
    )
    parser.add_argument("--dump", help="Dump the CDA database", action="store_true")
    parser.add_argument(
        "reports",
        type=str,
        nargs="+",
        help="bulk_extractor report directories or ZIP files",
    )
    parser.add_argument("-v", "--version", action='version', version=f"%(prog)s {__version__}")
    args = parser.parse_args()

    if args.makestop:
        if os.path.exists(args.makestop):
            raise IOError(f"{args.makestop}: file exists")
        if args.threshold < 0 or args.threshold > 1:
            raise RuntimeError(
                f"threshold should be between 0 and 1; you supplied {str(args.threshold)}"
            )

    # Create the correlators, one for each feature file
    correlators = set()
    for name in args.idfeatures.split(","):
        correlators.add(Correlator(name))

    # Create the br readers, one for each report
    br_readers = set()
    for fname in args.reports:
        # On windows the '*' may not be expanded....
        if "*" in fname:
            fns = glob.glob(fname)
        else:
            fns = [fname]
        for fn in fns:
            try:
                br_readers.add(bulk_extractor_reader.BulkReport(fn))
            except IOError:
                print(
                    f"{fn} is an invalid bulk_extractor report. Cannot continue. STOP.\n"
                )
                sys.exit(1)

    # Now read each feature file from each reader
    # Either ingest (in the case of cda) or create the context stop list (if making combined)
    for c in correlators:
        context_stop_list = set()
        for br in br_readers:
            b = br.open(f"{c.name}.txt", mode="r")
            if args.makecombined:
                count = c.ingest_feature_file(b, context_stop_list)
            else:
                count = c.ingest_feature_file(b, None)
        if args.makecombined:
            fn = f"combined-{c.name}.txt"
            with open(fn, mode="w") as f:
                for feature, context in context_stop_list:
                    f.write("".join(["", "\t", feature, "\t", context, "\n"]))
            print(f"Created {fn} with {len(context_stop_list)} lines\n")
            print("DONE")
            sys.exit(0)

    if args.dump:
        for c in correlators:
            c.dump_stats(sys.stdout)

    # Does the user want to make a stoplist?
    if args.makestop:
        stoplist = set()
        drive_threshold = int(float(len(args.reports)) * float(args.threshold))
        drives_per_feature = collections.defaultdict(int)
        for c in correlators:
            for feature, drives in c.features.items():
                drivecount = len(drives)
                drives_per_feature[drivecount] += 1
                if drivecount >= drive_threshold:
                    stoplist.add(feature)
        with open(args.makestop, "w") as f:
            for feature in sorted(stoplist):
                f.write(f"{feature}\n")
        print(f"Stoplist {args.makestop} created with {len(stoplist)} features")
        print("   DPF   Feature Count")
        for i in sorted(drives_per_feature.keys()):
            print(f"{i:6}    {drives_per_feature[i]:8}")
        print("--------------------")
        print("DPF = Drives per Feature")
        print(f"Only features on {drive_threshold} or more drives were written.")

    # Perhaps the user wants to perform identity-based correlation?
    # This will calculate a correlation coefficient between each pair of drives
    if args.idcor:
        print("Identity-based correlation: computes drive affinity using TF-IDF")

        # First get a list of all the drives
        drives_all = set()
        for c in correlators:
            drives_all = drives_all.union(c.drives)

        # Now compute the affinity between all the drives
        # ((driveA,driveB),score) added
        scores = []

        for driveA in drives_all:
            for driveB in drives_all:
                if driveA >= driveB:
                    continue  # don't auto-correlate
                score = 0.0
                factors = (
                    []
                )  # keep track of which are the most important factors for this pair
                for c in correlators:
                    for feature, drives in c.features.items():
                        if driveA in drives and driveB in drives:
                            factor = 1.0 / len(drives)
                            score += factor
                            factors.append((factor, feature))
                factors.sort(key=lambda a: -int(a[0]))
                scores.append(((driveA, driveB), score, factors[0:5]))
        scores.sort(key=lambda a: -a[1])
        for (a, b), score, factors in scores:
            if score == 0:
                continue
            print(f"Drive A: {a}")
            print(f"Drive B: {b}")
            print(f"Score  : {score}")
            print("Top factors:")
            for a, b in factors[0:10]:
                print(f"        {a:1.4}  {b}")
            print("")

    # A network map shows all of the Mac addresses and all of the packets that were carved.
    # We restrict the map to IP addresses


if __name__ == "__main__":
    main()
