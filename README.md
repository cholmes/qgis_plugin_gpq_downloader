# GeoParquet Downloader for QGIS

This repo contains a QGIS plugin for downloading GeoParquet data from cloud sources, including Overture Maps, Source Cooperative, and the ability to enter the location of any online GeoParquet file or partition. Just the user's current viewport then gets downloaded, as GeoParquet, DuckDB or GeoPackage.

![gpq-downloader-demo4](https://github.com/user-attachments/assets/10f2a73f-2aa6-45a1-9491-41e63b7fec24)


The core idea is that GeoParquet can act more like a 'server', letting users download only the data they need, if you add a bit more smarts to the client. So this plugin uses [DuckDB](https://duckdb.org/) but abstracts all the details of forming the right queries to external sources, so users can just pick the data they want and pull it down with ease. And with GeoPackage output users don't even need to know anything about GeoParquet. More info is on the [plugin homepage](https://plugins.qgis.org/plugins/qgis_plugin_gpq_downloader/).


## Installation

The easiest way to install the plugin file is to use the QGIS plugin manager. Just go to `Plugins > Manage and Install Plugins`, click 
the 'install' tab and search for 'GeoParquet Downloader'. Click on 'Install Plugin' and it will install. Alternatively you can download the zip file from
one of the [releases](https://github.com/cholmes/qgis_plugin_gpq_downloader/releases) and 'install from zip' in QGIS. For the plugin to work DuckDB
needs to be installed. As of version 0.3 the plugin should try to automatically install DuckDB, but it doesn't work reliably. If you installed but don't see the 
icon below then it's likely because DuckDB isn't there.

If the installation of DuckDB doesn't work, then on Windows you can use the [QDuckDB plugin](https://oslandia.gitlab.io/qgis/qduckdb/) which includes a precompiled binary. 
They also document how to install DuckDB on [Linux](https://oslandia.gitlab.io/qgis/qduckdb/usage/installation.html#linux) and 
[Mac OS/X](https://oslandia.gitlab.io/qgis/qduckdb/usage/installation.html#macos). If you're on Mac we recommend trying 
the [QGIS 4.0 mac build preview](https://github.com/opengisch/qgis-notarize/) which ships with DuckDB.

See [metadata.txt](gpq_downloader/metadata.txt) for more installation notes.

## Usage

The plugin will install 1 button on the "Plugin" QGIS toolbar, that you might have to enable through `View > Toolbars > Plugins`:

![1_UuUno32b4P_UNUqJZvSPoQ](https://github.com/user-attachments/assets/16003294-9a76-42cb-a740-b5bbd308e484)

It opens a dialog box, that lets you select Overture and Source Cooperative, Hugging Face or 'custom' - where you 
can enter the location of any GeoParquet or partition file online.

<img width="564" height="236" alt="Screenshot 2025-11-11 at 9 36 33 PM" src="https://github.com/user-attachments/assets/895f5ac4-58e9-42ba-b998-fedd5b21f15d" />


To use it move to an area where you'd like to download data and then select which layer you'd like to download. From there you can choose the output format (GeoParquet, GeoPackage, DuckDB, GeoJSON or FlatGeobuf) and the location to download the data to.

Downloads can sometimes take awhile, especially if the data provider hasn't optimized their GeoParquet files very well, or if you're downloading an area with a lot of data. Overture is one of the faster ones for now, others may take a minute or two. But it should most always be faster than trying to figure out exactly which files you need and downloading them manually.

For now we only support downloading into the current viewport, but hope to [improve that](https://github.com/cholmes/qgis_plugin_gpq_downloader/issues/10). Note also that right now only lat/long data is supported, but we also hope to [support it](https://github.com/cholmes/qgis_plugin_gpq_downloader/issues/102).

If your QGIS doesn't have GeoParquet support you'll get a warning dialog after the data downloads completes. The GeoParquet will be there, but it won't automatically open on the map. We definitely recommend getting your QGIS working with GeoParquet, as the format is faster and handles nested attributes better. See [Installing GeoParquet Support in QGIS](https://github.com/cholmes/qgis_plugin_gpq_downloader/wiki/Installing-GeoParquet-Support-in-QGIS) for more details.


## Contributing

This plugin has been made entirely with AI coding tools (primarily Cursor with claude-3.5-sonnet). Contributions are very welcome, both from more experienced python developers who can help clean up the code and add missing features, and from anyone who wants a place to do AI-assisted coding that (hopefully) actually gets widely used.

I'm interested in exploring open source collaboration in the age of AI coding tools, especially working with less experienced developers who'd like to contribute, so don't hesitate to jump in with AI-assisted pull requests.

And any help on ideas/feedback, documentation, testing, promoting, etc. is very welcome!


 
