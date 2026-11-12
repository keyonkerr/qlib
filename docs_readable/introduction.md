# `Qlib`: Quantitative Platform

## Introduction

<img src="../_static/img/logo/white_bg_rec+word.png" class="align-center" alt="image" />

`Qlib` is an AI-oriented quantitative investment platform, which aims to realize the potential, empower the research, and create the value of AI technologies in quantitative investment.

With `Qlib`, users can easily try their ideas to create better Quant investment strategies.

## Framework

<img src="../_static/img/framework.svg" class="align-center" alt="image" />

At the module level, Qlib is a platform that consists of above components. The components are designed as loose-coupled modules and each component could be used stand-alone.

This framework may be intimidating for new users to Qlib. It tries to accurately include a lot of details of Qlib's design. For users new to Qlib, you can skip it first and read it later.

| Name | Description |
|----|----|
| <span class="title-ref">Infrastructure</span> layer | <span class="title-ref">Infrastructure</span> layer provides underlying support for Quant research. <span class="title-ref">DataServer</span> provides high-performance infrastructure for users to manage and retrieve raw data. <span class="title-ref">Trainer</span> provides flexible interface to control the training process of models which enable algorithms controlling the training process. |
| <span class="title-ref">Learning Framework</span> layer | The <span class="title-ref">Forecast Model</span> and <span class="title-ref">Trading Agent</span> are trainable. They are trained based on the <span class="title-ref">Learning Framework</span> layer and then applied to multiple scenarios in <span class="title-ref">Workflow</span> layer. The supported learning paradigms can be categorized into reinforcement learning and supervised learning. The learning framework leverages the <span class="title-ref">Workflow</span> layer as well(e.g. sharing <span class="title-ref">Information Extractor</span>, creating environments based on <span class="title-ref">Execution Env</span>). |
| <span class="title-ref">Workflow</span> layer | <span class="title-ref">Workflow</span> layer covers the whole workflow of quantitative investment. Both supervised-learning-based strategies and RL-based Strategies are supported. <span class="title-ref">Information Extractor</span> extracts data for models. <span class="title-ref">Forecast Model</span> focuses on producing all kinds of forecast signals (e.g. *alpha*, risk) for other modules. With these signals <span class="title-ref">Decision Generator</span> will generate the target trading decisions(i.e. portfolio, orders) If RL-based Strategies are adopted, the <span class="title-ref">Policy</span> is learned in a end-to-end way, the trading decisions are generated directly. Decisions will be executed by <span class="title-ref">Execution Env</span> (i.e. the trading market). There may be multiple levels of <span class="title-ref">Strategy</span> and <span class="title-ref">Executor</span> (e.g. an *order executor trading strategy and intraday order executor* could behave like an interday trading loop and be nested in *daily portfolio management trading strategy and interday trading executor* trading loop) |
| <span class="title-ref">Interface</span> layer | <span class="title-ref">Interface</span> layer tries to present a user-friendly interface for the underlying system. <span class="title-ref">Analyser</span> module will provide users detailed analysis reports of forecasting signals, portfolios and execution results |

- The modules with hand-drawn style are under development and will be released in the future.
- The modules with dashed borders are highly user-customizable and extendible.

(p.s. framework image is created with <https://draw.io/>)
