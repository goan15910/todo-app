---- MODULE TodoTask ----
(***************************************************************************)
(* One task from Clew's b-full chart                                      *)
(* (examples/todo/b-full.yaml in https://github.com/goan15910/clew).      *)
(* Each action is one chart edge. TitleNotEmpty and CompletedAtSet are    *)
(* that chart's guards, lifted to state invariants.                       *)
(*                                                                         *)
(* `submitted` is a history variable. The chart has no such field. It     *)
(* records that draft --SUBMIT--> todo has happened, which is the only    *)
(* edge guarded by titleNotEmpty. Restore of a deleted draft reaches      *)
(* `todo` without that guard, so "todo implies a non-empty title" is not  *)
(* an invariant.                                                           *)
(*                                                                         *)
(* Title is "" or the representative "task". The guard only reads         *)
(* emptiness, and no action changes the title, so one non-empty value is  *)
(* enough. Timestamps are "unset" or "set": the guards and effects talk   *)
(* about presence, not clock values. A draft starts unlisted; markListed  *)
(* is what lists it.                                                       *)
(***************************************************************************)

States == {
  "draft", "todo", "inProgress", "snoozed", "done", "archived", "deleted"
}
Titles == {"", "task"}
Marks == {"unset", "set"}

VARIABLE task

TypeOK ==
  task \in [
    state: States,
    title: Titles,
    listed: BOOLEAN,
    started_at: Marks,
    completed_at: Marks,
    wake_at: Marks,
    deleted_at: Marks,
    submitted: BOOLEAN
  ]

\* Guard titleNotEmpty, reads title, on draft --SUBMIT--> todo.
TitleNotEmpty == task.submitted => (task.title /= "")

\* Guard completedAtSet, reads completed_at, on done --ARCHIVE--> archived.
CompletedAtSet == (task.state = "archived") => (task.completed_at /= "unset")

Init ==
  \E chosen \in Titles:
    task = [
      state |-> "draft",
      title |-> chosen,
      listed |-> FALSE,
      started_at |-> "unset",
      completed_at |-> "unset",
      wake_at |-> "unset",
      deleted_at |-> "unset",
      submitted |-> FALSE
    ]

DraftSubmit ==
  /\ task.state = "draft"
  /\ task.title /= ""
  /\ task' = [task EXCEPT !.state = "todo", !.listed = TRUE, !.submitted = TRUE]

DraftDelete ==
  /\ task.state = "draft"
  /\ task' = [task EXCEPT !.state = "deleted", !.deleted_at = "set"]

TodoStart ==
  /\ task.state = "todo"
  /\ task' = [task EXCEPT !.state = "inProgress", !.started_at = "set"]

TodoComplete ==
  /\ task.state = "todo"
  /\ task' = [task EXCEPT !.state = "done", !.completed_at = "set"]

TodoSnooze ==
  /\ task.state = "todo"
  /\ task' = [task EXCEPT !.state = "snoozed", !.wake_at = "set"]

TodoDelete ==
  /\ task.state = "todo"
  /\ task' = [task EXCEPT !.state = "deleted", !.deleted_at = "set"]

InProgressSnooze ==
  /\ task.state = "inProgress"
  /\ task' = [task EXCEPT !.state = "snoozed", !.wake_at = "set"]

InProgressComplete ==
  /\ task.state = "inProgress"
  /\ task' = [task EXCEPT !.state = "done", !.completed_at = "set"]

SnoozedResume ==
  /\ task.state = "snoozed"
  /\ task' = [task EXCEPT !.state = "inProgress", !.wake_at = "unset"]

DoneReopen ==
  /\ task.state = "done"
  /\ task' = [task EXCEPT !.state = "todo", !.completed_at = "unset"]

DoneArchive ==
  /\ task.state = "done"
  /\ task.completed_at /= "unset"
  /\ task' = [task EXCEPT !.state = "archived", !.listed = FALSE]

ArchivedUnarchive ==
  /\ task.state = "archived"
  /\ task' = [task EXCEPT !.state = "done", !.listed = TRUE]

DeletedRestore ==
  /\ task.state = "deleted"
  /\ task' = [task EXCEPT !.state = "todo", !.deleted_at = "unset"]

Next ==
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

Spec == Init /\ [][Next]_task

=============================================================================
