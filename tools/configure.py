#    Copyright 2022 Cryspen Sarl
#
#    Licensed under the Apache License, Version 2.0 or MIT.
#    * http://www.apache.org/licenses/LICENSE-2.0
#    * http://opensource.org/licenses/MIT

import os
import json
import re
import subprocess
from os.path import join
from os import sep as separator
from glob import glob


class Config:

    def dependencies(self, source_dir, algorithm, source_file):
        """Collect dependencies for a given c file

        Use `$CC -MM` to collect dependencies for a given c file assuming header
        and source files are named the same.
        """
        # Build dependency graph
        # FIXME: read include paths and CC from config.json
        includes = '-I ' + ' -I '.join(self.include_paths)
        result = subprocess.run(
            self.compiler + ' ' + includes + ' -I' +
            join(source_dir, 'internal') + ' -MM ' +
            join(source_dir, source_file),
            stdout=subprocess.PIPE,
            shell=True,
            check=True)
        stdout = result.stdout.decode('utf-8')

        files = []
        for line in stdout.splitlines():
            # Remove object file and the c file itself
            first_line_search = "(\w*).o: " + \
                re.escape(join(source_dir, "(\w*).c"))
            line = re.sub(first_line_search, "", line)
            line = line.strip()
            line = line.split(' ')
            try:
                line.remove("\\")
            except:
                # This is fine
                pass
            files.extend(line)

        # Get all source files in source_dir
        source_files = glob(join(source_dir, "*.c"))
        # remove source_dir and .c
        source_files = list(
            map(lambda s: s[len(source_dir)+1:-2], source_files))

        # Now let's collect the c files from the included headers
        # This adds all files without looking at the feature requirements into deps.
        deps = []
        includes = []
        for include in files:
            # Get the file name from the path
            file_name = os.path.splitext(os.path.basename(include))[0]
            # Only add the dependency if there's a corresponding source file.
            if file_name in source_files:
                deps.append(join(source_dir, file_name+".c"))
            # We take all includes though
            if include.endswith(".h"):
                includes.append(include)
        return deps, includes

    def __init__(self, config_file, source_dir, include_dir, algorithms=[], compiler='clang'):
        """Read the build config from the json file"""
        print(" [mach] Using %s to configure ..." % (config_file))
        if len(algorithms) != 0:
            print(" [mach]   enabling %s" % " ".join(algorithms))

        # read file
        with open(config_file, 'r') as f:
            data = f.read()

        self.compiler = compiler

        # parse file
        self.config = json.loads(data)
        self.hacl_files = self.config["hacl_sources"]
        self.evercrypt_files = self.config["evercrypt_sources"]
        self.vale_files = self.config["vale_sources"]
        self.tests = self.config["tests"]

        self.include_paths = [include_dir]
        # We need the config.h generated by CMake
        self.include_paths.append("build")
        # Set kremlin as include paths
        self.include_paths.extend(self.config["karamel_include_paths"])
        # If vale is compiled add the include path
        if len(self.vale_files) != 0:
            self.include_paths.extend(self.config["vale_include_paths"])

        # Filter algorithms in hacl_files
        # In the default case (empty list of algorithms) we don't do anything.
        if len(algorithms) != 0:
            # Check if the algorithms are actually valid
            for alg in algorithms:
                if not alg in self.hacl_files:
                    print(" [mach] ! Unsupported algorithm requested: %s" % alg)
                    exit(1)
            for a, _ in list(self.hacl_files.items()):
                if not a in algorithms:
                    del self.hacl_files[a]
            for a, _ in list(self.evercrypt_files.items()):
                if not a in algorithms:
                    del self.evercrypt_files[a]
            for a, _ in list(self.tests.items()):
                if not a in algorithms:
                    del self.tests[a]
            for a, _ in list(self.vale_files.items()):
                if not a in algorithms and a != "std":
                    del self.vale_files[a]

        # Collect dependencies for the hacl files.
        self.hacl_compile_feature = {}
        self.hacl_includes = []
        for a in self.hacl_files:
            for source_file in self.hacl_files[a]:
                files, includes = self.dependencies(
                    source_dir, a, source_file["file"])
                self.hacl_includes.extend(includes if type(
                    includes) == list else [includes])
                feature = source_file["features"]
                if feature in self.hacl_compile_feature:
                    self.hacl_compile_feature[feature].extend(
                        files if type(files) == list else [files])
                else:
                    # Add the new feature dependency
                    self.hacl_compile_feature[feature] = files if type(files) == list else [
                        files]
        # Remove files that require additional features from hacl_compile_files
        for feature in self.hacl_compile_feature:
            if feature != "std":
                # Filter all feature files to remove std files.
                self.hacl_compile_feature[feature] = [
                    file for file in self.hacl_compile_feature[feature] if file not in self.hacl_compile_feature["std"]]

        # Flatten test sources
        self.test_sources = [f for files in [self.tests[b]
                                             for b in self.tests] for f in files]

        # Flatten vale files into a single list for each platform.
        # This is all or nothing.
        platforms = {}
        for algorithm in self.vale_files:
            for p in self.vale_files[algorithm]:
                if p in platforms:
                    platforms[p].extend(self.vale_files[algorithm][p])
                else:
                    platforms[p] = self.vale_files[algorithm][p]
        for p in platforms:
            platforms[p] = [join("vale", "src", f) for f in platforms[p]]
        self.vale_files = platforms

        # Evercrypt has feature detection and we don't disable anything.
        self.evercrypt_compile_files = []
        for a in self.evercrypt_files:
            for source_file in self.evercrypt_files[a]:
                files, includes = self.dependencies(source_dir, a, source_file)
                self.evercrypt_compile_files.extend(files)
                self.hacl_includes.extend(includes if type(
                    includes) == list else [includes])

        # Remove duplicates from all lists
        for k in self.hacl_compile_feature:
            self.hacl_compile_feature[k] = list(
                dict.fromkeys(self.hacl_compile_feature[k]))
        self.evercrypt_compile_files = list(
            dict.fromkeys(self.evercrypt_compile_files))
        self.hacl_includes = list(dict.fromkeys(self.hacl_includes))
        # Drop Hacl_ files from evercrypt
        self.evercrypt_compile_files = [
            f for f in self.evercrypt_compile_files if "Hacl_" not in f]
        self.hacl_compile_feature['std'].extend(self.evercrypt_compile_files)

        # We don't want internal excludes to be installed.
        self.public_includes = [file for file in self.hacl_includes if join(
            "internal", os.path.basename(file)) not in file]

    def write_cmake_config(self, cmake_config):
        print(" [mach] Writing cmake config to %s ..." % (cmake_config))
        # cmake wants the unix style for paths apparently
        with open(cmake_config, 'w') as out:
            for a in self.hacl_compile_feature:
                out.write("set(SOURCES_%s %s)\n" %
                          (a, " ".join(join("${PROJECT_SOURCE_DIR}", f) for f in self.hacl_compile_feature[a]).replace(separator, '/')))

            out.write("set(INCLUDES %s)\n" %
                      " ".join(join("${PROJECT_SOURCE_DIR}", a) for a in self.hacl_includes).replace(separator, '/'))

            out.write("set(PUBLIC_INCLUDES %s)\n" %
                      " ".join(join("${PROJECT_SOURCE_DIR}", a) for a in self.public_includes).replace(separator, '/'))

            out.write("set(ALGORITHMS %s)\n" %
                      " ".join(a for a in self.hacl_files).replace(separator, '/'))

            out.write("set(INCLUDE_PATHS %s)\n" %
                      " ".join(join("${PROJECT_SOURCE_DIR}", p) for p in self.include_paths).replace(separator, '/'))

            out.write("set(TEST_SOURCES %s)\n" %
                      (" ".join(join("${PROJECT_SOURCE_DIR}", "tests", f) for f in self.test_sources).replace(separator, '/')))

            for os in self.vale_files:
                out.write("set(VALE_SOURCES_%s %s)\n" %
                          (os, " ".join(join("${PROJECT_SOURCE_DIR}", f) for f in self.vale_files[os]).replace(separator, '/')))

            out.write("set(ALGORITHM_TEST_FILES %s)\n" %
                      " ".join("TEST_FILES_"+a for a in self.tests).replace(separator, '/'))
            for a in self.tests:
                out.write("set(TEST_FILES_%s %s)\n" %
                          (a, " ".join(f for f in self.tests[a]).replace(separator, '/')))

    def dep_config(self):
        print(" [mach] Collecting files and dependencies ...")
        includes = [
            include for include in self.hacl_includes if not include.startswith("kremlin") and not include.startswith("vale")]
        vale_includes = [
            include for include in self.hacl_includes if include.startswith("vale")]
        kremlin_includes = [
            include for include in self.hacl_includes if include.startswith("kremlin")]
        return {
            "sources": self.hacl_compile_feature,
            "includes": includes,
            "kremlin_includes": kremlin_includes,
            "vale_sources": self.vale_files,
            "vale_includes": vale_includes,
        }

    def write_dep_config(self, dep_config):
        config = self.dep_config()
        json_data = json.dumps(config, indent=4)
        with open(dep_config, "w") as outfile:
            outfile.write(json_data)

    def source_files(self):
        """Get a list of all source files in the config."""
        out = []
        # FIXME
        # for a in self.hacl_compile_files:
        #     out.extend(self.hacl_compile_files[a])
        for a in self.evercrypt_compile_files:
            out.extend(self.evercrypt_compile_files[a])
        return out

    # TODO: we first have to create a list of headers
    def header_files(self):
        """Get a list of all header files in the config."""
        pass
