---- MODULE TodoTask ----
(***************************************************************************)
(* A bounded task repository. Each task follows the b-full lifecycle: the  *)
(* same status changes as the one-task chart, and the same field writes    *)
(* on each change. The repository can add a task, fetch one by id, and     *)
(* list the ids that have a given status.                                  *)
(*                                                                         *)
(* MaxTasks in the config bounds how many tasks TLC explores. Titles are   *)
(* "" or "task" because submit only reads emptiness, and nothing changes   *)
(* a title after it is chosen. Timestamps are "unset" or "set".            *)
(*                                                                         *)
(* `submitted` is history. The chart has no such field. It records that    *)
(* draft --SUBMIT--> todo has happened, which is the only step guarded by  *)
(* a non-empty title. Restoring a deleted draft reaches todo without that  *)
(* step, so a todo may still have an empty title.                          *)
(*                                                                         *)
(* Get and Listed do not change the repository. The query invariants say   *)
(* what they return.                                                       *)
(***************************************************************************)

EXTENDS Integers

CONSTANT MaxTasks

ASSUME MaxTasks \in Nat /\ MaxTasks >= 1

States == {
  "draft", "todo", "inProgress", "snoozed", "done", "archived", "deleted"
}
Titles == {"", "task"}
Marks == {"unset", "set"}
Missing == "missing"

VARIABLES tasks, seq

Draft(title) ==
  [
    state |-> "draft",
    title |-> title,
    listed |-> FALSE,
    started_at |-> "unset",
    completed_at |-> "unset",
    wake_at |-> "unset",
    deleted_at |-> "unset",
    submitted |-> FALSE
  ]

IsTask(task) ==
  /\ task.state \in States
  /\ task.title \in Titles
  /\ task.listed \in BOOLEAN
  /\ task.started_at \in Marks
  /\ task.completed_at \in Marks
  /\ task.wake_at \in Marks
  /\ task.deleted_at \in Marks
  /\ task.submitted \in BOOLEAN

IsStored(record) ==
  /\ record.id \in 1..MaxTasks
  /\ IsTask(record.task)

TypeOK ==
  /\ seq \in 1..(MaxTasks + 1)
  /\ \A record \in tasks: IsStored(record)
  /\ \A left, right \in tasks: left.id = right.id => left = right
  /\ \A record \in tasks: record.id < seq

IdsDense ==
  \A id \in 1..(seq - 1): \E record \in tasks: record.id = id

TitleNotEmpty ==
  \A record \in tasks: record.task.submitted => record.task.title /= ""

CompletedAtSet ==
  \A record \in tasks:
    (record.task.state = "archived") => (record.task.completed_at /= "unset")

DraftUnlisted ==
  \A record \in tasks:
    (record.task.state = "draft") =>
      /\ record.task.listed = FALSE
      /\ record.task.submitted = FALSE

ArchivedUnlisted ==
  \A record \in tasks:
    (record.task.state = "archived") => (record.task.listed = FALSE)

DeletedIffStamp ==
  \A record \in tasks:
    (record.task.state = "deleted") <=> (record.task.deleted_at = "set")

WakeOnlyWhileSnoozed ==
  \A record \in tasks:
    (record.task.wake_at = "set") => (record.task.state = "snoozed")

Get(id) ==
  IF \E record \in tasks: record.id = id
  THEN (CHOOSE record \in tasks: record.id = id).task
  ELSE Missing

Listed(status) ==
  {record.id : record \in {item \in tasks : item.task.state = status}}

ListedAll == {record.id : record \in tasks}

GetStored ==
  \A record \in tasks: Get(record.id) = record.task

\* A present id returns its task (GetStored). TLC will not compare that
\* record with the Missing marker, so absence is the direction checked here.
GetMissing ==
  \A id \in 1..MaxTasks:
    (~\E record \in tasks: record.id = id) => (Get(id) = Missing)

FilterCovers ==
  /\ ListedAll = UNION {Listed(status) : status \in States}
  /\ \A left, right \in States:
       left /= right => Listed(left) \intersect Listed(right) = {}

Init ==
  /\ tasks = {}
  /\ seq = 1

Add ==
  /\ seq <= MaxTasks
  /\ \E title \in Titles:
       /\ tasks' = tasks \union {[id |-> seq, task |-> Draft(title)]}
       /\ seq' = seq + 1

Replace(record, updated) ==
  /\ tasks' = (tasks \ {record}) \union {[record EXCEPT !.task = updated]}
  /\ UNCHANGED seq

DraftSubmit ==
  \E record \in tasks:
    /\ record.task.state = "draft"
    /\ record.task.title /= ""
    /\ Replace(record, [
         record.task EXCEPT !.state = "todo", !.listed = TRUE, !.submitted = TRUE
       ])

DraftDelete ==
  \E record \in tasks:
    /\ record.task.state = "draft"
    /\ Replace(record, [record.task EXCEPT !.state = "deleted", !.deleted_at = "set"])

TodoStart ==
  \E record \in tasks:
    /\ record.task.state = "todo"
    /\ Replace(record, [
         record.task EXCEPT !.state = "inProgress", !.started_at = "set"
       ])

TodoComplete ==
  \E record \in tasks:
    /\ record.task.state = "todo"
    /\ Replace(record, [record.task EXCEPT !.state = "done", !.completed_at = "set"])

TodoSnooze ==
  \E record \in tasks:
    /\ record.task.state = "todo"
    /\ Replace(record, [record.task EXCEPT !.state = "snoozed", !.wake_at = "set"])

TodoDelete ==
  \E record \in tasks:
    /\ record.task.state = "todo"
    /\ Replace(record, [record.task EXCEPT !.state = "deleted", !.deleted_at = "set"])

InProgressSnooze ==
  \E record \in tasks:
    /\ record.task.state = "inProgress"
    /\ Replace(record, [record.task EXCEPT !.state = "snoozed", !.wake_at = "set"])

InProgressComplete ==
  \E record \in tasks:
    /\ record.task.state = "inProgress"
    /\ Replace(record, [record.task EXCEPT !.state = "done", !.completed_at = "set"])

SnoozedResume ==
  \E record \in tasks:
    /\ record.task.state = "snoozed"
    /\ Replace(record, [
         record.task EXCEPT !.state = "inProgress", !.wake_at = "unset"
       ])

DoneReopen ==
  \E record \in tasks:
    /\ record.task.state = "done"
    /\ Replace(record, [record.task EXCEPT !.state = "todo", !.completed_at = "unset"])

DoneArchive ==
  \E record \in tasks:
    /\ record.task.state = "done"
    /\ record.task.completed_at /= "unset"
    /\ Replace(record, [record.task EXCEPT !.state = "archived", !.listed = FALSE])

ArchivedUnarchive ==
  \E record \in tasks:
    /\ record.task.state = "archived"
    /\ Replace(record, [record.task EXCEPT !.state = "done", !.listed = TRUE])

DeletedRestore ==
  \E record \in tasks:
    /\ record.task.state = "deleted"
    /\ Replace(record, [record.task EXCEPT !.state = "todo", !.deleted_at = "unset"])

Next ==
  \/ Add
  \/ DraftSubmit
  \/ DraftDelete
  \/ TodoStart
  \/ TodoComplete
  \/ TodoSnooze
  \/ TodoDelete
  \/ InProgressSnooze
  \/ InProgressComplete
  \/ SnoozedResume
  \/ DoneReopen
  \/ DoneArchive
  \/ ArchivedUnarchive
  \/ DeletedRestore

Spec == Init /\ [][Next]_<<tasks, seq>>

=============================================================================
