---- MODULE IssueTracker ----
(***************************************************************************)
(* A bounded issue tracker. A project is the aggregate: it owns a dense   *)
(* issue numbering, and every rule about issues holds inside one project. *)
(* Issues follow backlog -> todo -> inProgress -> inReview -> done, may be *)
(* canceled while open, and may be reopened once closed. An issue may be  *)
(* blocked by other issues of its project. The blocking graph has no      *)
(* cycles, and approval waits for every blocker to be closed.             *)
(*                                                                         *)
(* The one variable is what the application persists through its unit of  *)
(* work: the projects, by key, each with its issues by number. Numbers    *)
(* are dense because the tracker never deletes an issue. Titles are "" or *)
(* "text" because creation only reads emptiness. Timestamps are "unset"   *)
(* or "set". Comments are a count. Keys and users are small fixed sets.   *)
(*                                                                         *)
(* Every action is one committed unit of work on one project. An action   *)
(* whose guard is false is a refused command: the application rolls back  *)
(* and nothing changes. The trace checker uses both facts.                *)
(***************************************************************************)

EXTENDS Integers, FiniteSets, TLC

CONSTANTS Keys, Users, MaxIssues, MaxComments

ASSUME Keys /= {} /\ \A key \in Keys : key \in STRING
ASSUME Users /= {} /\ \A user \in Users : user \in STRING
ASSUME MaxIssues \in Nat /\ MaxIssues >= 1
ASSUME MaxComments \in Nat

Statuses == {"backlog", "todo", "inProgress", "inReview", "done", "canceled"}
Titles == {"", "text"}
Marks == {"unset", "set"}
Unassigned == "unset"

VARIABLE projects

Open(status) == status \in {"backlog", "todo", "inProgress", "inReview"}
Closed(status) == status \in {"done", "canceled"}
Active(status) == status \in {"inProgress", "inReview"}

Numbers(key) == DOMAIN projects[key].issues
Issue(key, n) == projects[key].issues[n]
NextNumber(key) == Cardinality(Numbers(key)) + 1

RECURSIVE Upstream(_, _, _)
Upstream(key, frontier, seen) ==
  IF frontier = {} THEN seen
  ELSE LET next == (UNION {Issue(key, j).blocked_by : j \in frontier}) \ seen
       IN Upstream(key, next, seen \union next)

\* Every issue that blocks n, directly or through other blockers.
TransitiveBlockers(key, n) ==
  Upstream(key, Issue(key, n).blocked_by, Issue(key, n).blocked_by)

IsIssue(key, record) ==
  /\ record.title \in Titles
  /\ record.status \in Statuses
  /\ record.assignee \in Users \union {Unassigned}
  /\ record.blocked_by \subseteq Numbers(key)
  /\ record.comments \in Nat
  /\ record.started_at \in Marks
  /\ record.completed_at \in Marks
  /\ record.canceled_at \in Marks

TypeOK ==
  /\ DOMAIN projects \subseteq Keys
  /\ \A key \in DOMAIN projects :
       /\ Numbers(key) = 1..Cardinality(Numbers(key))
       /\ Cardinality(Numbers(key)) <= MaxIssues
       /\ \A n \in Numbers(key) : IsIssue(key, Issue(key, n))

TitleNotEmpty ==
  \A key \in DOMAIN projects : \A n \in Numbers(key) : Issue(key, n).title /= ""

ActiveAssigned ==
  \A key \in DOMAIN projects : \A n \in Numbers(key) :
    Active(Issue(key, n).status) => Issue(key, n).assignee /= Unassigned

ActiveStarted ==
  \A key \in DOMAIN projects : \A n \in Numbers(key) :
    Active(Issue(key, n).status) => Issue(key, n).started_at = "set"

CompletedIffDone ==
  \A key \in DOMAIN projects : \A n \in Numbers(key) :
    (Issue(key, n).completed_at = "set") <=> (Issue(key, n).status = "done")

CanceledIffCanceled ==
  \A key \in DOMAIN projects : \A n \in Numbers(key) :
    (Issue(key, n).canceled_at = "set") <=> (Issue(key, n).status = "canceled")

NoSelfBlock ==
  \A key \in DOMAIN projects : \A n \in Numbers(key) : n \notin Issue(key, n).blocked_by

NoBlockCycles ==
  \A key \in DOMAIN projects : \A n \in Numbers(key) : n \notin TransitiveBlockers(key, n)

CommentsBounded ==
  \A key \in DOMAIN projects : \A n \in Numbers(key) : Issue(key, n).comments <= MaxComments

Init ==
  projects = [key \in {} |-> 0]

NewIssue(title) ==
  [
    title |-> title,
    status |-> "backlog",
    assignee |-> Unassigned,
    blocked_by |-> {},
    comments |-> 0,
    started_at |-> "unset",
    completed_at |-> "unset",
    canceled_at |-> "unset"
  ]

CreateProject(key) ==
  /\ key \in Keys
  /\ key \notin DOMAIN projects
  /\ projects' = projects @@ (key :> [issues |-> [n \in {} |-> 0]])

CreateIssue(key, title) ==
  /\ key \in DOMAIN projects
  /\ title \in Titles
  /\ title /= ""
  /\ Cardinality(Numbers(key)) < MaxIssues
  /\ projects' = [projects EXCEPT
       ![key].issues = @ @@ (NextNumber(key) :> NewIssue(title))]

Triage(key, n) ==
  /\ key \in DOMAIN projects
  /\ n \in Numbers(key)
  /\ Issue(key, n).status = "backlog"
  /\ projects' = [projects EXCEPT ![key].issues[n].status = "todo"]

Assign(key, n, user) ==
  /\ key \in DOMAIN projects
  /\ n \in Numbers(key)
  /\ user \in Users
  /\ Open(Issue(key, n).status)
  /\ Issue(key, n).assignee /= user
  /\ projects' = [projects EXCEPT ![key].issues[n].assignee = user]

Unassign(key, n) ==
  /\ key \in DOMAIN projects
  /\ n \in Numbers(key)
  /\ Issue(key, n).assignee /= Unassigned
  /\ ~Active(Issue(key, n).status)
  /\ projects' = [projects EXCEPT ![key].issues[n].assignee = Unassigned]

Start(key, n) ==
  /\ key \in DOMAIN projects
  /\ n \in Numbers(key)
  /\ Issue(key, n).status = "todo"
  /\ Issue(key, n).assignee /= Unassigned
  /\ projects' = [projects EXCEPT
       ![key].issues[n].status = "inProgress",
       ![key].issues[n].started_at = "set"]

SubmitReview(key, n) ==
  /\ key \in DOMAIN projects
  /\ n \in Numbers(key)
  /\ Issue(key, n).status = "inProgress"
  /\ projects' = [projects EXCEPT ![key].issues[n].status = "inReview"]

RequestChanges(key, n) ==
  /\ key \in DOMAIN projects
  /\ n \in Numbers(key)
  /\ Issue(key, n).status = "inReview"
  /\ projects' = [projects EXCEPT ![key].issues[n].status = "inProgress"]

Approve(key, n) ==
  /\ key \in DOMAIN projects
  /\ n \in Numbers(key)
  /\ Issue(key, n).status = "inReview"
  /\ \A b \in Issue(key, n).blocked_by : Closed(Issue(key, b).status)
  /\ projects' = [projects EXCEPT
       ![key].issues[n].status = "done",
       ![key].issues[n].completed_at = "set"]

Cancel(key, n) ==
  /\ key \in DOMAIN projects
  /\ n \in Numbers(key)
  /\ Open(Issue(key, n).status)
  /\ projects' = [projects EXCEPT
       ![key].issues[n].status = "canceled",
       ![key].issues[n].canceled_at = "set"]

Reopen(key, n) ==
  /\ key \in DOMAIN projects
  /\ n \in Numbers(key)
  /\ Closed(Issue(key, n).status)
  /\ projects' = [projects EXCEPT
       ![key].issues[n].status = "todo",
       ![key].issues[n].completed_at = "unset",
       ![key].issues[n].canceled_at = "unset"]

AddBlocker(key, n, b) ==
  /\ key \in DOMAIN projects
  /\ n \in Numbers(key)
  /\ b \in Numbers(key)
  /\ n /= b
  /\ Open(Issue(key, n).status)
  /\ b \notin Issue(key, n).blocked_by
  /\ n \notin TransitiveBlockers(key, b)
  /\ projects' = [projects EXCEPT ![key].issues[n].blocked_by = @ \union {b}]

RemoveBlocker(key, n, b) ==
  /\ key \in DOMAIN projects
  /\ n \in Numbers(key)
  /\ b \in Issue(key, n).blocked_by
  /\ projects' = [projects EXCEPT ![key].issues[n].blocked_by = @ \ {b}]

Comment(key, n) ==
  /\ key \in DOMAIN projects
  /\ n \in Numbers(key)
  /\ Issue(key, n).status /= "canceled"
  /\ projects' = [projects EXCEPT ![key].issues[n].comments = @ + 1]

Next ==
  \/ \E key \in Keys : CreateProject(key)
  \/ \E key \in DOMAIN projects :
       \/ \E title \in Titles : CreateIssue(key, title)
       \/ \E n \in Numbers(key) :
            \/ Triage(key, n)
            \/ \E user \in Users : Assign(key, n, user)
            \/ Unassign(key, n)
            \/ Start(key, n)
            \/ SubmitReview(key, n)
            \/ RequestChanges(key, n)
            \/ Approve(key, n)
            \/ Cancel(key, n)
            \/ Reopen(key, n)
            \/ \E b \in Numbers(key) : AddBlocker(key, n, b)
            \/ \E b \in Numbers(key) : RemoveBlocker(key, n, b)
            \/ Comment(key, n)

Spec == Init /\ [][Next]_projects

=============================================================================
