"""ReSkate map builder: writes the game's own package formats from plain geometry.

Pure Python (no compiled tools), so it runs under the signed python.exe on Windows
and on Linux CI. The format code is a port of ReSkate's Engine/Resource readers and
writers (toc.cpp, binary_bundle.cpp, cas_codec.cpp, ebx_document.cpp, ebx_writer.cpp), checked byte for byte against
them in tests/.
"""
