---
id: goals-friction
version: 0.3.0
archetype: ladder
experience_promise: >
  You finish knowing what you want help with, what gets in your way,
  and the one thing you will do next.
duration_min: 10
sensitivity: [career-goals, personal-obstacles, learning-preferences, felt-responses]
writes: [goals_plan]
routes: [reactions]
---

## card: care
explain: >
  You can start here even if your workspace is empty; no uploads or earlier
  interviews are needed. We start with what matters to you, because that is
  easier to answer than a goal. You can skip a question or stop at any point.
type: free_text
ask: What do you care about?
anchoring: none

## card: hard
explain: Next, what makes things hard. There are no right answers.
type: free_text
ask: What makes life hard right now?
anchoring: none

## card: stuck
explain: This one is about the habit of making progress, from starting to keeping going.
type: free_text
ask: When you try to move something forward, where do you get stuck?
anchoring: none
then: categorize

## card: learn
explain: This decides how suggestions are presented, including whether you see reasoning or a concrete first step first.
type: options
ask: How do you like to learn?
options: [Tell me what to do, Walk me through the reasoning, Show me examples first]
effect: show_rationale

## card: want
explain: Now the goal. Your earlier answers are shown so you can build on them.
type: free_text
ask: What would you like to do?
recall: [care, hard, stuck]
anchoring: none
exit:
  - label: I don't know yet
    route: reactions

## card: ladder
explain: Goals usually have a reason behind them, and the reason shapes what to do next.
type: free_text
ask_if_goal: Why does that matter to you?
ask_if_problem: What would be different if this were not the case?
anchoring: none
classify: goal_or_problem
repeat: { max: 3, until: user_stops }

## card: pick
explain: You may have named more than one goal. Working on one first keeps this manageable.
type: options
ask: Which one do you want to work on now?
options_from: ladder.goals

## card: prescribe
explain: These are possible moves for the kind of obstacle you selected, not a diagnosis. Choose only what fits, or choose your own step.
type: options
ask: Which of these will you try?
options_from: playbook(stuck)
max_picks: 3

## card: commit
explain: Choose one first step now. Other selected moves stay queued until you finish it.
type: commit_action
ask: What is the first step?
queue_rest: true
owner: user

## card: felt
explain: This checks how the next step lands for you. Your rating and reason stay in this workspace.
type: scale_then_free_text
ask: How do you feel about what is next?
stores_with: [loop_id, loop_version]

## playbook
- category: getting_started
  move: Make the task small enough to start
  first_step: Set aside ten minutes for the smallest useful piece
  rationale: A short attempt can reveal what you need without committing to the whole task.
- category: getting_started
  move: Remove one setup barrier
  first_step: Put the materials you need in one place
  rationale: Less preparation can make the next attempt easier to begin.
- category: too_much
  move: Choose one thing and deliberately park the rest
  first_step: Write down what can wait until tomorrow
  rationale: Reducing competing demands can make one next step clearer.
- category: unclear_next_step
  move: Find the missing information before planning more
  first_step: Write one question whose answer would let you move
  rationale: A concrete unknown is easier to investigate than a vague plan.
- category: keeping_going
  move: Attach a small repeatable step to an existing routine
  first_step: Choose the routine and one small action to follow it
  rationale: A familiar cue can help you remember to return to the work.
- category: confidence
  move: Try a low-stakes rehearsal
  first_step: Pick one small practice attempt in a setting you choose
  rationale: Practice can show what to adjust before a higher-stakes attempt.
- category: support
  move: Ask for a specific piece of help
  first_step: Choose whom to ask and the one question you want to ask
  rationale: A bounded request makes the help you need easier to explain.
- category: energy_or_time
  move: Fit the step to the time and energy you have
  first_step: Choose a short window and reduce the task to fit it
  rationale: A smaller commitment can be more realistic than asking yourself to do more.
