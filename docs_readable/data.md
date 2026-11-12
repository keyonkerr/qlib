# Data Layer: Data Framework & Usage

## Introduction

`Data Layer` provides user-friendly APIs to manage and retrieve data. It provides high-performance data infrastructure.

It is designed for quantitative investment. For example, users could build formulaic alphas with `Data Layer` easily. Please refer to [Building Formulaic Alphas](../advanced/alpha.html) for more details.

The introduction of `Data Layer` includes the following parts.

- Data Preparation
- Data API
- Data Loader
- Data Handler
- Dataset
- Cache
- Data and Cache File Structure

Here is a typical example of Qlib data workflow

- Users download data and converting data into Qlib format(with filename suffix <span class="title-ref">.bin</span>). In this step, typically only some basic data are stored on disk(such as OHLCV).
- Creating some basic features based on Qlib's expression Engine(e.g. "Ref(\$close, 60) / \$close", the return of last 60 trading days). Supported operators in the expression engine can be found [here](https://github.com/microsoft/qlib/blob/main/qlib/data/ops.py). This step is typically implemented in Qlib's [Data Loader](https://qlib.readthedocs.io/en/latest/component/data.html#data-loader) which is a component of [Data Handler](https://qlib.readthedocs.io/en/latest/component/data.html#data-handler) .
- If users require more complicated data processing (e.g. data normalization), [Data Handler](https://qlib.readthedocs.io/en/latest/component/data.html#data-handler) support user-customized processors to process data(some predefined processors can be found [here](https://github.com/microsoft/qlib/blob/main/qlib/data/dataset/processor.py)). The processors are different from operators in expression engine. It is designed for some complicated data processing methods which is hard to supported in operators in expression engine.
- At last, [Dataset](https://qlib.readthedocs.io/en/latest/component/data.html#dataset) is responsible to prepare model-specific dataset from the processed data of Data Handler

## Data Preparation

### Qlib Format Data

We've specially designed a data structure to manage financial data, please refer to the [File storage design section in Qlib paper](https://arxiv.org/abs/2009.11189) for detailed information. Such data will be stored with filename suffix <span class="title-ref">.bin</span> (We'll call them <span class="title-ref">.bin</span> file, <span class="title-ref">.bin</span> format, or qlib format). <span class="title-ref">.bin</span> file is designed for scientific computing on finance data.

`Qlib` provides two different off-the-shelf datasets, which can be accessed through this [link](https://github.com/microsoft/qlib/blob/main/qlib/contrib/data/handler.py):

| Dataset  | US Market | China Market |
|----------|-----------|--------------|
| Alpha360 | √         | √            |
| Alpha158 | √         | √            |

Also, `Qlib` provides a high-frequency dataset. Users can run a high-frequency dataset example through this [link](https://github.com/microsoft/qlib/tree/main/examples/highfreq).

### Qlib Format Dataset

`Qlib` has provided an off-the-shelf dataset in <span class="title-ref">.bin</span> format, users could use the script `scripts/get_data.py` to download the China-Stock dataset as follows. User can also use numpy to load <span class="title-ref">.bin</span> file to validate data. The price volume data look different from the actual dealing price because of they are **adjusted** ([adjusted price](https://www.investopedia.com/terms/a/adjusted_closing_price.asp)). And then you may find that the adjusted price may be different from different data sources. This is because different data sources may vary in the way of adjusting prices. Qlib normalize the price on first trading day of each stock to 1 when adjusting them. Users can leverage <span class="title-ref">\$factor</span> to get the original trading price (e.g. <span class="title-ref">\$close / \$factor</span> to get the original close price).

Here are some discussions about the price adjusting of Qlib.

- <https://github.com/microsoft/qlib/issues/991#issuecomment-1075252402>

``` bash
# download 1d
python scripts/get_data.py qlib_data --target_dir ~/.qlib/qlib_data/cn_data --region cn

# download 1min
python scripts/get_data.py qlib_data --target_dir ~/.qlib/qlib_data/qlib_cn_1min --region cn --interval 1min
```

In addition to China-Stock data, `Qlib` also includes a US-Stock dataset, which can be downloaded with the following command:

``` bash
python scripts/get_data.py qlib_data --target_dir ~/.qlib/qlib_data/us_data --region us
```

After running the above command, users can find china-stock and us-stock data in `Qlib` format in the `~/.qlib/qlib_data/cn_data` directory and `~/.qlib/qlib_data/us_data` directory respectively.

`Qlib` also provides the scripts in `scripts/data_collector` to help users crawl the latest data on the Internet and convert it to qlib format.

When `Qlib` is initialized with this dataset, users could build and evaluate their own models with it. Please refer to [Initialization](../start/initialization.html) for more details.

### Automatic update of daily frequency data

> **It is recommended that users update the data manually once (--trading_date 2021-05-25) and then set it to update automatically.**
>
> For more information refer to: [yahoo collector](https://github.com/microsoft/qlib/tree/main/scripts/data_collector/yahoo#Automatic-update-of-daily-frequency-data)
>
> - Automatic update of data to the "qlib" directory each trading day(Linux)  
>   - use *crontab*: <span class="title-ref">crontab -e</span>
>
>   - set up timed tasks:
>
>     ``` bash
>     * * * * 1-5 python <script path> update_data_to_bin --qlib_data_1d_dir <user data dir>
>     ```
>
>     - **script path**: *scripts/data_collector/yahoo/collector.py*
>
> - Manual update of data
>
>   > ``` bash
>   > python scripts/data_collector/yahoo/collector.py update_data_to_bin --qlib_data_1d_dir <user data dir> --trading_date <start date> --end_date <end date>
>   > ```
>   >
>   > - *trading_date*: start of trading day
>   > - *end_date*: end of trading day(not included)

### Converting CSV and Parquet Format into Qlib Format

`Qlib` has provided the script `scripts/dump_bin.py` to convert **any** data in CSV or Parquet format into <span class="title-ref">.bin</span> files (`Qlib` format) as long as they are in the correct format.

Besides downloading the prepared demo data, users could download demo data directly from the Collector as follows for reference to the CSV format. Here are some example:

for daily data:  
``` bash
python scripts/get_data.py download_data --file_name csv_data_cn.zip --target_dir ~/.qlib/csv_data/cn_data
```

for 1min data:  
``` bash
python scripts/data_collector/yahoo/collector.py download_data --source_dir ~/.qlib/stock_data/source/cn_1min --region CN --start 2021-05-20 --end 2021-05-23 --delay 0.1 --interval 1min --limit_nums 10
```

Users can also provide their own data in CSV or Parquet format. However, the data **must satisfies** following criterions:

- CSV or Parquet file is named after a specific stock *or* the CSV or Parquet file includes a column of the stock name

  > - Name the CSV or Parquet file after a stock: <span class="title-ref">SH600000.csv</span>, <span class="title-ref">AAPL.csv</span> or <span class="title-ref">SH600000.parquet</span>, <span class="title-ref">AAPL.parquet</span> (not case sensitive).
  >
  > - CSV or Parquet file includes a column of the stock name. User **must** specify the column name when dumping the data. Here is an example:
  >
  >   > ``` bash
  >   > python scripts/dump_bin.py dump_all ... --symbol_field_name symbol --file_suffix <.csv or .parquet>
  >   > ```
  >   >
  >   > where the data are in the following format:
  >   >
  >   > > | symbol   | close |
  >   > > |----------|-------|
  >   > > | SH600000 | 120   |

- CSV or Parquet file **must** include a column for the date, and when dumping the data, user must specify the date column name. Here is an example:

  > ``` bash
  > python scripts/dump_bin.py dump_all ... --date_field_name date --file_suffix <.csv or .parquet>
  > ```
  >
  > where the data are in the following format:
  >
  > > | symbol   | date       | close | open | volume   |
  > > |----------|------------|-------|------|----------|
  > > | SH600000 | 2020-11-01 | 120   | 121  | 12300000 |
  > > | SH600000 | 2020-11-02 | 123   | 120  | 12300000 |

Supposed that users prepare their CSV or Parquet format data in the directory `~/.qlib/my_data`, they can run the following command to start the conversion.

``` bash
python scripts/dump_bin.py dump_all --data_path  ~/.qlib/my_data --qlib_dir ~/.qlib/qlib_data/ --include_fields open,close,high,low,volume,factor --file_suffix <.csv or .parquet>
```

For other supported parameters when dumping the data into <span class="title-ref">.bin</span> file, users can refer to the information by running the following commands:

``` bash
python scripts/dump_bin.py dump_all --help
```

After conversion, users can find their Qlib format data in the directory <span class="title-ref">~/.qlib/qlib_data/</span>.

> [!NOTE]
> The arguments of <span class="title-ref">--include_fields</span> should correspond with the column names of CSV or Parquet files. The columns names of dataset provided by `Qlib` should include open, close, high, low, volume and factor at least.
>
> - <span class="title-ref">open</span>  
>   The adjusted opening price
>
> - <span class="title-ref">close</span>  
>   The adjusted closing price
>
> - <span class="title-ref">high</span>  
>   The adjusted highest price
>
> - <span class="title-ref">low</span>  
>   The adjusted lowest price
>
> - <span class="title-ref">volume</span>  
>   The adjusted trading volume
>
> - <span class="title-ref">factor</span>  
>   The Restoration factor. Normally, `factor = adjusted_price / original_price`, <span class="title-ref">adjusted price</span> reference: [split adjusted](https://www.investopedia.com/terms/s/splitadjusted.asp)
>
> In the convention of <span class="title-ref">Qlib</span> data processing, <span class="title-ref">open, close, high, low, volume, money and factor</span> will be set to NaN if the stock is suspended. If you want to use your own alpha-factor which can't be calculate by OCHLV, like PE, EPS and so on, you could add it to the CSV or Parquet files with OHCLV together and then dump it to the Qlib format data.

### Checking the health of the data

`Qlib` provides a script to check the health of the data.

- The main points to check are as follows

  > - Check if any data is missing in the DataFrame.
  > - Check if there are any large step changes above the threshold in the OHLCV columns.
  > - Check if any of the required columns (OLHCV) are missing in the DataFrame.
  > - Check if the 'factor' column is missing in the DataFrame.

- You can run the following commands to check whether the data is healthy or not.

  > for daily data:  
  > ``` bash
  > python scripts/check_data_health.py check_data --qlib_dir ~/.qlib/qlib_data/cn_data
  > ```
  >
  > for 1min data:  
  > ``` bash
  > python scripts/check_data_health.py check_data --qlib_dir ~/.qlib/qlib_data/cn_data_1min --freq 1min
  > ```

- Of course, you can also add some parameters to adjust the test results.

  > - The available parameters are these.
  >
  >   > - freq: Frequency of data.
  >   > - large_step_threshold_price: Maximum permitted price change
  >   > - large_step_threshold_volume: Maximum permitted volume change.
  >   > - missing_data_num: Maximum value for which data is allowed to be null.

- You can run the following commands to check whether the data is healthy or not.

  > for daily data:  
  > ``` bash
  > python scripts/check_data_health.py check_data --qlib_dir ~/.qlib/qlib_data/cn_data --missing_data_num 30055 --large_step_threshold_volume 94485 --large_step_threshold_price 20
  > ```
  >
  > for 1min data:  
  > ``` bash
  > python scripts/check_data_health.py check_data --qlib_dir ~/.qlib/qlib_data/cn_data --freq 1min --missing_data_num 35806 --large_step_threshold_volume 3205452000000 --large_step_threshold_price 0.91
  > ```

### Stock Pool (Market)

`Qlib` defines [stock pool](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml#L4) as stock list and their date ranges. Predefined stock pools (e.g. csi300) may be imported as follows.

``` bash
python collector.py --index_name CSI300 --qlib_dir <user qlib data dir> --method parse_instruments
```

### Multiple Stock Modes

`Qlib` now provides two different stock modes for users: China-Stock Mode & US-Stock Mode. Here are some different settings of these two modes:

| Region | Trade Unit | Limit Threshold |
|--------|------------|-----------------|
| China  | 100        | 0.099           |
| US     | 1          | None            |

The <span class="title-ref">trade unit</span> defines the unit number of stocks can be used in a trade, and the <span class="title-ref">limit threshold</span> defines the bound set to the percentage of ups and downs of a stock.

- If users use `Qlib` in china-stock mode, china-stock data is required. Users can use `Qlib` in china-stock mode according to the following steps:  
  - Download china-stock in qlib format, please refer to section [Qlib Format Dataset](#qlib-format-dataset).

  - Initialize `Qlib` in china-stock mode  
    Supposed that users download their Qlib format data in the directory `~/.qlib/qlib_data/cn_data`. Users only need to initialize `Qlib` as follows.

    ``` python
    from qlib.constant import REG_CN
    qlib.init(provider_uri='~/.qlib/qlib_data/cn_data', region=REG_CN)
    ```

- If users use `Qlib` in US-stock mode, US-stock data is required. `Qlib` also provides a script to download US-stock data. Users can use `Qlib` in US-stock mode according to the following steps:  
  - Download us-stock in qlib format, please refer to section [Qlib Format Dataset](#qlib-format-dataset).

  - Initialize `Qlib` in US-stock mode  
    Supposed that users prepare their Qlib format data in the directory `~/.qlib/qlib_data/us_data`. Users only need to initialize `Qlib` as follows.

    ``` python
    from qlib.config import REG_US
    qlib.init(provider_uri='~/.qlib/qlib_data/us_data', region=REG_US)
    ```

> [!NOTE]
> PRs for new data source are highly welcome! Users could commit the code to crawl data as a PR like [the examples here](https://github.com/microsoft/qlib/tree/main/scripts). And then we will use the code to create data cache on our server which other users could use directly.

## Data API

### Data Retrieval

Users can use APIs in `qlib.data` to retrieve data, please refer to [Data Retrieval](../start/getdata.html).

### Feature

`Qlib` provides <span class="title-ref">Feature</span> and <span class="title-ref">ExpressionOps</span> to fetch the features according to users' needs.

- <span class="title-ref">Feature</span>  
  Load data from the data provider. User can get the features like <span class="title-ref">\$high</span>, <span class="title-ref">\$low</span>, <span class="title-ref">\$open</span>, <span class="title-ref">\$close</span>, .etc, which should correspond with the arguments of <span class="title-ref">--include_fields</span>, please refer to section [Converting CSV Format into Qlib Format](#converting-csv-format-into-qlib-format).

- <span class="title-ref">ExpressionOps</span>  
  <span class="title-ref">ExpressionOps</span> will use operator for feature construction. To know more about `Operator`, please refer to [Operator API](../reference/api.html#module-qlib.data.ops). Also, `Qlib` supports users to define their own custom `Operator`, an example has been given in `tests/test_register_ops.py`.

To know more about `Feature`, please refer to [Feature API](../reference/api.html#module-qlib.data.base).

### Filter

`Qlib` provides <span class="title-ref">NameDFilter</span> and <span class="title-ref">ExpressionDFilter</span> to filter the instruments according to users' needs.

- <span class="title-ref">NameDFilter</span>  
  Name dynamic instrument filter. Filter the instruments based on a regulated name format. A name rule regular expression is required.

- <span class="title-ref">ExpressionDFilter</span>  
  Expression dynamic instrument filter. Filter the instruments based on a certain expression. An expression rule indicating a certain feature field is required.

  - \`basic features filter\`: rule_expression = '\$close/\$open\>5'
  - <span class="title-ref">cross-sectional features filter</span> : rule_expression = '\$rank(\$close)\<10'
  - \`time-sequence features filter\`: rule_expression = '\$Ref(\$close, 3)\>100'

Here is a simple example showing how to use filter in a basic `Qlib` workflow configuration file:

``` yaml
filter: &filter
    filter_type: ExpressionDFilter
    rule_expression: "Ref($close, -2) / Ref($close, -1) > 1"
    filter_start_time: 2010-01-01
    filter_end_time: 2010-01-07
    keep: False

data_handler_config: &data_handler_config
    start_time: 2010-01-01
    end_time: 2021-01-22
    fit_start_time: 2010-01-01
    fit_end_time: 2015-12-31
    instruments: *market
    filter_pipe: [*filter]
```

To know more about `Filter`, please refer to [Filter API](../reference/api.html#module-qlib.data.filter).

### Reference

To know more about `Data API`, please refer to [Data API](../reference/api.html#data).

## Data Loader

`Data Loader` in `Qlib` is designed to load raw data from the original data source. It will be loaded and used in the `Data Handler` module.

### QlibDataLoader

The `QlibDataLoader` class in `Qlib` is such an interface that allows users to load raw data from the `Qlib` data source.

### StaticDataLoader

The `StaticDataLoader` class in `Qlib` is such an interface that allows users to load raw data from file or as provided.

### Interface

Here are some interfaces of the `QlibDataLoader` class:

<div class="autoclass" members="" noindex="">

qlib.data.dataset.loader.DataLoader

</div>

### API

To know more about `Data Loader`, please refer to [Data Loader API](../reference/api.html#module-qlib.data.dataset.loader).

## Data Handler

The `Data Handler` module in `Qlib` is designed to handler those common data processing methods which will be used by most of the models.

Users can use `Data Handler` in an automatic workflow by `qrun`, refer to [Workflow: Workflow Management](workflow.html) for more details.

### DataHandlerLP

In addition to use `Data Handler` in an automatic workflow with `qrun`, `Data Handler` can be used as an independent module, by which users can easily preprocess data (standardization, remove NaN, etc.) and build datasets.

In order to achieve so, `Qlib` provides a base class [qlib.data.dataset.DataHandlerLP](../reference/api.html#qlib.data.dataset.handler.DataHandlerLP). The core idea of this class is that: we will have some learnable `Processors` which can learn the parameters of data processing(e.g., parameters for zscore normalization). When new data comes in, these <span class="title-ref">trained</span> `Processors` can then process the new data and thus processing real-time data in an efficient way becomes possible. More information about `Processors` will be listed in the next subsection.

### Interface

Here are some important interfaces that `DataHandlerLP` provides:

<div class="autoclass" members="__init__, fetch, get_cols" noindex="">

qlib.data.dataset.handler.DataHandlerLP

</div>

If users want to load features and labels by config, users can define a new handler and call the static method <span class="title-ref">parse_config_to_fields</span> of `qlib.contrib.data.handler.Alpha158`.

Also, users can pass `qlib.contrib.data.processor.ConfigSectionProcessor` that provides some preprocess methods for features defined by config into the new handler.

### Processor

The `Processor` module in `Qlib` is designed to be learnable and it is responsible for handling data processing such as <span class="title-ref">normalization</span> and <span class="title-ref">drop none/nan features/labels</span>.

`Qlib` provides the following `Processors`:

- `DropnaProcessor`: <span class="title-ref">processor</span> that drops N/A features.
- `DropnaLabel`: <span class="title-ref">processor</span> that drops N/A labels.
- `TanhProcess`: <span class="title-ref">processor</span> that uses <span class="title-ref">tanh</span> to process noise data.
- `ProcessInf`: <span class="title-ref">processor</span> that handles infinity values, it will be replaces by the mean of the column.
- `Fillna`: <span class="title-ref">processor</span> that handles N/A values, which will fill the N/A value by 0 or other given number.
- `MinMaxNorm`: <span class="title-ref">processor</span> that applies min-max normalization.
- `ZscoreNorm`: <span class="title-ref">processor</span> that applies z-score normalization.
- `RobustZScoreNorm`: <span class="title-ref">processor</span> that applies robust z-score normalization.
- `CSZScoreNorm`: <span class="title-ref">processor</span> that applies cross sectional z-score normalization.
- `CSRankNorm`: <span class="title-ref">processor</span> that applies cross sectional rank normalization.
- `CSZFillna`: <span class="title-ref">processor</span> that fills N/A values in a cross sectional way by the mean of the column.

Users can also create their own <span class="title-ref">processor</span> by inheriting the base class of `Processor`. Please refer to the implementation of all the processors for more information ([Processor Link](https://github.com/microsoft/qlib/blob/main/qlib/data/dataset/processor.py)).

To know more about `Processor`, please refer to [Processor API](../reference/api.html#module-qlib.data.dataset.processor).

### Example

`Data Handler` can be run with `qrun` by modifying the configuration file, and can also be used as a single module.

Know more about how to run `Data Handler` with `qrun`, please refer to [Workflow: Workflow Management](workflow.html)

Qlib provides implemented data handler <span class="title-ref">Alpha158</span>. The following example shows how to run <span class="title-ref">Alpha158</span> as a single module.

> [!NOTE]
> Users need to initialize `Qlib` with <span class="title-ref">qlib.init</span> first, please refer to [initialization](../start/initialization.html).

``` Python
import qlib
from qlib.contrib.data.handler import Alpha158

data_handler_config = {
    "start_time": "2008-01-01",
    "end_time": "2020-08-01",
    "fit_start_time": "2008-01-01",
    "fit_end_time": "2014-12-31",
    "instruments": "csi300",
}

if __name__ == "__main__":
    qlib.init()
    h = Alpha158(**data_handler_config)

    # get all the columns of the data
    print(h.get_cols())

    # fetch all the labels
    print(h.fetch(col_set="label"))

    # fetch all the features
    print(h.fetch(col_set="feature"))
```

> [!NOTE]
> In the `Alpha158`, `Qlib` uses the label <span class="title-ref">Ref(\$close, -2)/Ref(\$close, -1) - 1</span> that means the change from T+1 to T+2, rather than <span class="title-ref">Ref(\$close, -1)/\$close - 1</span>, of which the reason is that when getting the T day close price of a china stock, the stock can be bought on T+1 day and sold on T+2 day.

### API

To know more about `Data Handler`, please refer to [Data Handler API](../reference/api.html#module-qlib.data.dataset.handler).

## Dataset

The `Dataset` module in `Qlib` aims to prepare data for model training and inferencing.

The motivation of this module is that we want to maximize the flexibility of different models to handle data that are suitable for themselves. This module gives the model the flexibility to process their data in an unique way. For instance, models such as `GBDT` may work well on data that contains <span class="title-ref">nan</span> or <span class="title-ref">None</span> value, while neural networks such as `MLP` will break down on such data.

If user's model need process its data in a different way, user could implement his own `Dataset` class. If the model's data processing is not special, `DatasetH` can be used directly.

The `DatasetH` class is the <span class="title-ref">dataset</span> with <span class="title-ref">Data Handler</span>. Here is the most important interface of the class:

<div class="autoclass" members="" noindex="">

qlib.data.dataset.\_\_init\_\_.DatasetH

</div>

### API

To know more about `Dataset`, please refer to [Dataset API](../reference/api.html#dataset).

## Cache

`Cache` is an optional module that helps accelerate providing data by saving some frequently-used data as cache file. `Qlib` provides a <span class="title-ref">Memcache</span> class to cache the most-frequently-used data in memory, an inheritable <span class="title-ref">ExpressionCache</span> class, and an inheritable <span class="title-ref">DatasetCache</span> class.

### Global Memory Cache

<span class="title-ref">Memcache</span> is a global memory cache mechanism that composes of three <span class="title-ref">MemCacheUnit</span> instances to cache **Calendar**, **Instruments**, and **Features**. The <span class="title-ref">MemCache</span> is defined globally in <span class="title-ref">cache.py</span> as <span class="title-ref">H</span>. Users can use <span class="title-ref">H\['c'\], H\['i'\], H\['f'\]</span> to get/set <span class="title-ref">memcache</span>.

<div class="autoclass" members="" noindex="">

qlib.data.cache.MemCacheUnit

</div>

<div class="autoclass" members="" noindex="">

qlib.data.cache.MemCache

</div>

### ExpressionCache

<span class="title-ref">ExpressionCache</span> is a cache mechanism that saves expressions such as **Mean(\$close, 5)**. Users can inherit this base class to define their own cache mechanism that saves expressions according to the following steps.

- Override <span class="title-ref">self.\_uri</span> method to define how the cache file path is generated
- Override <span class="title-ref">self.\_expression</span> method to define what data will be cached and how to cache it.

The following shows the details about the interfaces:

<div class="autoclass" members="" noindex="">

qlib.data.cache.ExpressionCache

</div>

`Qlib` has currently provided implemented disk cache <span class="title-ref">DiskExpressionCache</span> which inherits from <span class="title-ref">ExpressionCache</span> . The expressions data will be stored in the disk.

### DatasetCache

<span class="title-ref">DatasetCache</span> is a cache mechanism that saves datasets. A certain dataset is regulated by a stock pool configuration (or a series of instruments, though not recommended), a list of expressions or static feature fields, the start time, and end time for the collected features and the frequency. Users can inherit this base class to define their own cache mechanism that saves datasets according to the following steps.

- Override <span class="title-ref">self.\_uri</span> method to define how their cache file path is generated
- Override <span class="title-ref">self.\_expression</span> method to define what data will be cached and how to cache it.

The following shows the details about the interfaces:

<div class="autoclass" members="" noindex="">

qlib.data.cache.DatasetCache

</div>

`Qlib` has currently provided implemented disk cache <span class="title-ref">DiskDatasetCache</span> which inherits from <span class="title-ref">DatasetCache</span> . The datasets' data will be stored in the disk.

## Data and Cache File Structure

We've specially designed a file structure to manage data and cache, please refer to the [File storage design section in Qlib paper](https://arxiv.org/abs/2009.11189) for detailed information. The file structure of data and cache is listed as follows.

``` 
- data/
    [raw data] updated by data providers
    - calendars/
        - day.txt
    - instruments/
        - all.txt
        - csi500.txt
        - ...
    - features/
        - sh600000/
            - open.day.bin
            - close.day.bin
            - ...
        - ...
    [cached data] updated when raw data is updated
    - calculated features/
        - sh600000/
            - [hash(instrtument, field_expression, freq)]
                - all-time expression -cache data file
                - .meta : an assorted meta file recording the instrument name, field name, freq, and visit times
        - ...
    - cache/
        - [hash(stockpool_config, field_expression_list, freq)]
            - all-time Dataset-cache data file
            - .meta : an assorted meta file recording the stockpool config, field names and visit times
            - .index : an assorted index file recording the line index of all calendars
        - ...
```
