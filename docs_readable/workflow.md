# Workflow: Workflow Management

<div class="currentmodule">

qlib

</div>

## Introduction

The components in [Qlib Framework](../introduction/introduction.html#framework) are designed in a loosely-coupled way. Users could build their own Quant research workflow with these components like [Example](https://github.com/microsoft/qlib/blob/main/examples/workflow_by_code.py).

Besides, `Qlib` provides more user-friendly interfaces named `qrun` to automatically run the whole workflow defined by configuration. Running the whole workflow is called an <span class="title-ref">execution</span>. With `qrun`, user can easily start an <span class="title-ref">execution</span>, which includes the following steps:

- Data  
  - Loading
  - Processing
  - Slicing

- Model  
  - Training and inference
  - Saving & loading

- Evaluation  
  - Forecast signal analysis
  - Backtest

For each <span class="title-ref">execution</span>, `Qlib` has a complete system to tracking all the information as well as artifacts generated during training, inference and evaluation phase. For more information about how `Qlib` handles this, please refer to the related document: [Recorder: Experiment Management](../component/recorder.html).

## Complete Example

Before getting into details, here is a complete example of `qrun`, which defines the workflow in typical Quant research. Below is a typical config file of `qrun`.

``` YAML
qlib_init:
    provider_uri: "~/.qlib/qlib_data/cn_data"
    region: cn
market: &market csi300
benchmark: &benchmark SH000300
data_handler_config: &data_handler_config
    start_time: 2008-01-01
    end_time: 2020-08-01
    fit_start_time: 2008-01-01
    fit_end_time: 2014-12-31
    instruments: *market
port_analysis_config: &port_analysis_config
    strategy:
        class: TopkDropoutStrategy
        module_path: qlib.contrib.strategy.strategy
        kwargs:
            topk: 50
            n_drop: 5
            signal: <PRED>
    backtest:
        start_time: 2017-01-01
        end_time: 2020-08-01
        account: 100000000
        benchmark: *benchmark
        exchange_kwargs:
            limit_threshold: 0.095
            deal_price: close
            open_cost: 0.0005
            close_cost: 0.0015
            min_cost: 5
task:
    model:
        class: LGBModel
        module_path: qlib.contrib.model.gbdt
        kwargs:
            loss: mse
            colsample_bytree: 0.8879
            learning_rate: 0.0421
            subsample: 0.8789
            lambda_l1: 205.6999
            lambda_l2: 580.9768
            max_depth: 8
            num_leaves: 210
            num_threads: 20
    dataset:
        class: DatasetH
        module_path: qlib.data.dataset
        kwargs:
            handler:
                class: Alpha158
                module_path: qlib.contrib.data.handler
                kwargs: *data_handler_config
            segments:
                train: [2008-01-01, 2014-12-31]
                valid: [2015-01-01, 2016-12-31]
                test: [2017-01-01, 2020-08-01]
    record:
        - class: SignalRecord
          module_path: qlib.workflow.record_temp
          kwargs: {}
        - class: PortAnaRecord
          module_path: qlib.workflow.record_temp
          kwargs:
              config: *port_analysis_config
```

After saving the config into <span class="title-ref">configuration.yaml</span>, users could start the workflow and test their ideas with a single command below.

``` bash
qrun configuration.yaml
```

If users want to use `qrun` under debug mode, please use the following command:

``` bash
python -m pdb qlib/cli/run.py examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml
```

> [!NOTE]
> <span class="title-ref">qrun</span> will be placed in your \$PATH directory when installing `Qlib`.

> [!NOTE]
> The symbol <span class="title-ref">&</span> in <span class="title-ref">yaml</span> file stands for an anchor of a field, which is useful when another fields include this parameter as part of the value. Taking the configuration file above as an example, users can directly change the value of <span class="title-ref">market</span> and <span class="title-ref">benchmark</span> without traversing the entire configuration file.

## Configuration File

Let's get into details of `qrun` in this section. Before using `qrun`, users need to prepare a configuration file. The following content shows how to prepare each part of the configuration file.

The design logic of the configuration file is very simple. It predefines fixed workflows and provide this yaml interface to users to define how to initialize each component. It follow the design of [init_instance_by_config](https://github.com/microsoft/qlib/blob/2aee9e0145decc3e71def70909639b5e5a6f4b58/qlib/utils/__init__.py#L264) . It defines the initialization of each component of Qlib, which typically include the class and the initialization arguments.

For example, the following yaml and code are equivalent.

``` YAML
model:
    class: LGBModel
    module_path: qlib.contrib.model.gbdt
    kwargs:
        loss: mse
        colsample_bytree: 0.8879
        learning_rate: 0.0421
        subsample: 0.8789
        lambda_l1: 205.6999
        lambda_l2: 580.9768
        max_depth: 8
        num_leaves: 210
        num_threads: 20
```

``` python
from qlib.contrib.model.gbdt import LGBModel
kwargs = {
    "loss": "mse" ,
    "colsample_bytree": 0.8879,
    "learning_rate": 0.0421,
    "subsample": 0.8789,
    "lambda_l1": 205.6999,
    "lambda_l2": 580.9768,
    "max_depth": 8,
    "num_leaves": 210,
    "num_threads": 20,
}
LGBModel(kwargs)
```

### Qlib Init Section

At first, the configuration file needs to contain several basic parameters which will be used for qlib initialization.

``` YAML
provider_uri: "~/.qlib/qlib_data/cn_data"
region: cn
```

The meaning of each field is as follows:

- <span class="title-ref">provider_uri</span>  
  Type: str. The URI of the Qlib data. For example, it could be the location where the data loaded by `get_data.py` are stored.

- <span class="title-ref">region</span>  
  - If <span class="title-ref">region</span> == "us", `Qlib` will be initialized in US-stock mode.
  - If <span class="title-ref">region</span> == "cn", `Qlib` will be initialized in China-stock mode.

  > [!NOTE]
  > The value of <span class="title-ref">region</span> should be aligned with the data stored in <span class="title-ref">provider_uri</span>.

### Task Section

The <span class="title-ref">task</span> field in the configuration corresponds to a <span class="title-ref">task</span>, which contains the parameters of three different subsections: <span class="title-ref">Model</span>, <span class="title-ref">Dataset</span> and <span class="title-ref">Record</span>.

#### Model Section

In the <span class="title-ref">task</span> field, the <span class="title-ref">model</span> section describes the parameters of the model to be used for training and inference. For more information about the base `Model` class, please refer to [Qlib Model](../component/model.html).

``` YAML
model:
    class: LGBModel
    module_path: qlib.contrib.model.gbdt
    kwargs:
        loss: mse
        colsample_bytree: 0.8879
        learning_rate: 0.0421
        subsample: 0.8789
        lambda_l1: 205.6999
        lambda_l2: 580.9768
        max_depth: 8
        num_leaves: 210
        num_threads: 20
```

The meaning of each field is as follows:

- <span class="title-ref">class</span>  
  Type: str. The name for the model class.

- <span class="title-ref">module_path</span>  
  Type: str. The path for the model in qlib.

- <span class="title-ref">kwargs</span>  
  The keywords arguments for the model. Please refer to the specific model implementation for more information: [models](https://github.com/microsoft/qlib/blob/main/qlib/contrib/model).

> [!NOTE]
> `Qlib` provides a util named: `init_instance_by_config` to initialize any class inside `Qlib` with the configuration includes the fields: <span class="title-ref">class</span>, <span class="title-ref">module_path</span> and <span class="title-ref">kwargs</span>.

#### Dataset Section

The <span class="title-ref">dataset</span> field describes the parameters for the `Dataset` module in `Qlib` as well those for the module `DataHandler`. For more information about the `Dataset` module, please refer to [Qlib Data](../component/data.html#dataset).

The keywords arguments configuration of the `DataHandler` is as follows:

``` YAML
data_handler_config: &data_handler_config
    start_time: 2008-01-01
    end_time: 2020-08-01
    fit_start_time: 2008-01-01
    fit_end_time: 2014-12-31
    instruments: *market
```

Users can refer to the document of [DataHandler](../component/data.html#datahandler) for more information about the meaning of each field in the configuration.

Here is the configuration for the `Dataset` module which will take care of data preprocessing and slicing during the training and testing phase.

``` YAML
dataset:
    class: DatasetH
    module_path: qlib.data.dataset
    kwargs:
        handler:
            class: Alpha158
            module_path: qlib.contrib.data.handler
            kwargs: *data_handler_config
        segments:
            train: [2008-01-01, 2014-12-31]
            valid: [2015-01-01, 2016-12-31]
            test: [2017-01-01, 2020-08-01]
```

#### Record Section

The <span class="title-ref">record</span> field is about the parameters the `Record` module in `Qlib`. `Record` is responsible for tracking training process and results such as <span class="title-ref">information Coefficient (IC)</span> and <span class="title-ref">backtest</span> in a standard format.

The following script is the configuration of <span class="title-ref">backtest</span> and the <span class="title-ref">strategy</span> used in \`backtest\`:

``` YAML
port_analysis_config: &port_analysis_config
    strategy:
        class: TopkDropoutStrategy
        module_path: qlib.contrib.strategy.strategy
        kwargs:
            topk: 50
            n_drop: 5
            signal: <PRED>
    backtest:
        limit_threshold: 0.095
        account: 100000000
        benchmark: *benchmark
        deal_price: close
        open_cost: 0.0005
        close_cost: 0.0015
        min_cost: 5
```

For more information about the meaning of each field in configuration of <span class="title-ref">strategy</span> and <span class="title-ref">backtest</span>, users can look up the documents: [Strategy](../component/strategy.html) and [Backtest](../component/backtest.html).

Here is the configuration details of different <span class="title-ref">Record Template</span> such as `SignalRecord` and `PortAnaRecord`:

``` YAML
record:
    - class: SignalRecord
      module_path: qlib.workflow.record_temp
      kwargs: {}
    - class: PortAnaRecord
      module_path: qlib.workflow.record_temp
      kwargs:
        config: *port_analysis_config
```

For more information about the `Record` module in `Qlib`, user can refer to the related document: [Record](../component/recorder.html#record-template).
