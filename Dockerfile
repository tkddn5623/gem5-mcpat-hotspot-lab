FROM ghcr.io/gem5/ubuntu-24.04_all-dependencies:v26-0

# gdb for debugging gem5; Python libs for the glue scripts and plots;
# xz-utils is needed by the pwndbg installer below
RUN apt-get update && apt-get install -y --no-install-recommends \
        gdb \
        python3-lxml \
        python3-pandas \
        python3-matplotlib \
        xz-utils `# only for the pwndbg installer below` \
    && rm -rf /var/lib/apt/lists/*

# pwndbg as a separate "pwndbg" command (bundles its own patched GDB);
# plain "gdb" stays vanilla. Pin a release with "-v <version>" if needed.
# RUN curl --proto '=https' --tlsv1.2 -LsSf https://install.pwndbg.re \
#         | sh -s -- -t pwndbg-gdb