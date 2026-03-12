# Overview

This project is my first forray into agentic application building. 

We are building a multi-agent learning platform where, at a high level, agents handle the varied, and largely ambiguous task of teaching a student. 

## Notes to self
- save figures in-line in the conversations. 
    - definitely with written exercises
    - PDFs:
        - annotate the pdf pages 
        - save with annotated fnames for later
        - save with with a txt summary of pages for retreival in case leaves context
- MAKE PICS ZOOMABLE!
- AUDIO INPUT SUCKS
    - queue chunks of sound
        - until recording button pressed again to stop recording
    - pop_send

## Status

Currently, this application is in prototypical infancy. We have implemented a multi-agent system, and it provides a reasonably good UX for learning. 

### Addressed challenges

These may not be FULLY addressed in the sense that we'll never revisit them.

- First multi-agent application prototyped!
- Code-base cleanup (modularity from monolith)

### Unaddressed challenges

- "Production-ish" MVP prototype
    - covered in this document.
- Tool use issues.
- "teaching" modalities.
    - could be more "rich" UI/UX
    - e.g. image gen for visual aids.
- Adabtable teaching style
    - somewhat done by conversational context...
    - would be interesting to experiment with 'social science expert' agents, that evaluate the users interaction to evaluate things like:
        - What works for this user?
        - What do they seem engaged with?
        - strengths and weaknesses 

## What we've learned!

A lot!

### Agentic design choices

- Use deterministic solutions when available
    - more predictable / reliable
    - faster
    - cheaper
- use agents to handle:
    - ambiguous decision making scenarios
        - e.g. making lesson plans based on pdf content
    - flexible tool use
        - e.g. invoking a callable surface for a written exercise
    - "human-like" UX elements
        - e.g. conversation
- Start with most capable models first
    - establish a testable baseline
    - optimization:
        - compute cost and speed vs output quality

### Agents


### AI Coding pals
- Claude
    - Opus-4.6
    - Excelent. 
    - Planning is a must...
    - Example Work-flow:
        1) Think like a human.
            - Also... Think like a fancy search.
        2) Check yourself with fancy search
            - Sill... Think like a human.
        3) Plan with fancy search
            - Sill... Think like a human.
        4) Check fancy search with a different fancy search
            - Sill... Think like a human.
        5) Repeat 1-5 until all fancy searches and humans are confident to proceed with implementation
            - And implement
    - Gets stuck in loops. 
        - If stuck. Try a new agent with fresh context.  
        - Consider telling claude to read the docs before writing code...
        - Be specific with instructions...
        - log errors!

# Task list

## IN PROGRESS

### Client-server refactor

```markdown
# Overview
We want to further separate concerns. we want to build a client-server model, where the client only handles GUI presentation, and servers handle computationally significant operations and managing persistent data. 

## Definitions and abbreviations

- MVP (minimally viable product): Here I am refering to a prototype that can be presented to users to assess product viability
- UX (User Experience): How satisfying is this product for the end user to interact with?
- UI (User Interface): The elements the end user will interact with directly.
- Concerns: depending on context, I am referring to either - a consideration for design or implementation OR a process handled by some component of the system

## General Considerations

This section serves as guiding principles for product design, product execution and collaboration.

- General philosophy: 
    - Prioritize modularity, scalability, maintainability, portability and readability in both design choices and code generation. 
    - Adhere to language and framework conventions and best practices, especially when well established.
    - You are rich in knowledge. Contest choices of mine unless they seem well reasoned to you. Propose better ideas if you have them, but do not immediately change course without collaborating with me.
- Note taking and documentation
    - I have created a .notes/ directory. It contains a subdirectory for you and one for me, so that we may both keep our own notes. I ask that you do not edit my notes without explicit instruction and subsequent confirmation. 
    - You should make notes in your folder about unaddressed items in our plan, issues, todolists, guidelines, reminders, etc... It is a place for your eyes, so that you and I stay on task and on the same page over the long term.
- Rapid prototyping vs robust design:
    - We must balance the tradeoff between quickly building a prototyle to show potential users or investors with the concerns of becoming production ready. 
    - Although we may not immediately implement every production feature, we should plan with production in mind.
- Verbosity vs readability:
    - Write clean, human readable code. 
    - Use clear naming patterns and consistent style. 
    - Clearly document code, expecially when complex.
- Test-driven development
    - We should use test-driven design principles
    - Unless there is really no need, we should build automated test modules, separated by project feature category.
        - E.g. A module for testing db operations, a separate module for testing agentic functions, etc.
        - test modules should accompany the module they test, for organization purposes.
    - we should maintain runnable suites of tests
        - E.g. ALL db operation tests comprise a complete test suite, smaller suites may exist (like all GET operations in a relational db).
        - These suites should produce human readable documents.
- Security:
    - Security should be a paramount concern during the development process. 
    - Not a major prototyping concern with respect to implementation, but we should make design choices that plan ahead for security concerns.
    - We will perform security audits when the time comes. 
    - We should design automated penetration testing scripts when the time comes. They should produce audit documents.
- Tech debt:
    - We must always consider the trade-offs between solution simplicity, robustness, and reliance on fragile dependency chains.
    - Be aware of dependency conflicts. Attempt to address them through environment management. For instance, if we build a wrapper that can invoke multiple machine learning models for the same task, depending on configuration, one approach might be to separate these environments by creating one API for instantiating one model or the other so that they don't exist in the same runtime environment. 
    - We should generally opt for existing optimized solutions, rather than implementing from scratch during prototyping, with the exception of necessarily custom code implementation solutions.
        - We can reassess tech debt when we move beyond prototyping.
- Web vs Mobile development
    - Web comes first.
        - React is a great front-end option for its widespread support, rich community and package ecosystem.
    - This would make a good mobile app. 
        - For performance, we may want to consider platform native development when we get to that point.
    - Servers
        - We should make design choices such that backend servers can handle data for both web and mobile development.

## Client concerns

Clients send a server only data such as user input (light weight stt or tts **MIGHT** be acceptable, simplifying client-server data transmission to text and (potentially) improving UX (delayed latency). whisper-base, and kokoro are both working satisfactorily on MY hardware... although whisper may be GPU accellerated). 

We should first focus on 

## Server concerns

- Computationally expensive opperations (e.g. AI concerns)
    - We might grow into the use of locally (or cloud-compute) run models... 
    - That would create an entirely new set of concerns. We should consider them now though so that we plan wisely for growth. 
- DBMS operations
- VERY WELL may merit creating multiple APIs for separate concerns

## Endpoint Exposure, Communication/Data-transmission

Clients should communicate directly with a front-end server that prepares display relevant data to send to the user. 

## API

All clients-server and server-server communications should be handled through clean, compact APIs that act as thin wrappers for modules. Middleware / module imports can support maintainability.

The front end servers should communicate with a backend server to handle AI functionality and other compute intensive tasks, and 'database' operations (which we will migrate to an actual DBMS after this step is complete). It is worth noting that we may well want to architect this project so that we have separate servers handle DBMS and AI concerns, to plan for scalability.

## Scalability, future-proofing, and moving to market

Eventually, we may want to productize. Hence, we may want to handle concerns such as cloud deployment, load balancing, sharding and data accessibility, latency in a real environment, etc. 

Producing an MVP quickly takes precendence *at this stage*. However, we should discuss these items in consideration of future growth. 

The main trade-off to consider on this broad topic is the speed/ease of MVP creation vs speed/ease of moving from MVP -> production ready, we should implement a plan that considers this trade-off carefully. We do not want to overcomplicate MVP development, but we do not want to simplify to the point of making the transition painful.

## Instructions

- Before implementing any changes, we will discuss approaches for system design and refactoring this codebase to a client-server architecture for a functional prototype MVP. to move this application to production, including user accounts (authentication and security concerns, db schemas, etc, etc). Once we agree upon a thorough approach, create detailed notes on our design choices in a markdown file, then construct and execute a plan to implement the design. Take note that we want to use a test driven development process for this step.
```

### DBMS implementation

```markdown
We want to handle persistent data using a DBMS. I think that for now, we can create a sqlite3 API, reachable on another server instance in python. Before we implement anything, let's discuss approaches for handling data for this application, considering that we want to move this application towards production, including the management of user accounts. Once we agree upon a thorough approach, create detailed notes on our design choices in a markdown file, then construct and execute a plan to implement the design. Take note that we want to use a test driven development process for this step.
```

### Web app

``` markdown
We want to refactor this project to offer users a sleek, modern UI/UX. This will likely focus on transitioning the presentation layer to REACT. interactive web elements should be detectable by autofill such as a password manager.

Before we implement anything, let's discuss front-end system design choices for this project. Once we agree upon a thorough approach, create detailed notes on our design choices in a markdown file, then construct and execute a plan to implement the design. Take note that we want to use a test driven development process for this step.
```

## TO DO

### Users

``` markdown

We must enable the use of this application for users such that a user can- create an account, login to that account, access data specific to their account.

## Goals
- Account creation
- Authentication
    - MFA
        - Choose one simple MFA approach to implement first
            - OTP text or email seems like a decent option
    - Secure cross-session login persistence should be built in from the start
    - Web elements should be reachable from a password manager
- payment / revenue generation
    - Usage or subscription based revenue structure?
    - probably need to test...
```

### Mobile app

``` markdown
We should develop a mobile application for this. We should go with native Android development first, because it is more accessible from a development standpoint. We should be able to connect the same backend as the web app, but it is unclear whether or not the same front end servers will be sufficient for the web application.

Before we implement anything, let's discuss front-end system design choices for this project. Once we agree upon a thorough approach, create detailed notes on our design choices in a markdown file, then construct and execute a plan to implement the design. Take note that we want to use a test driven development process for this step.
```