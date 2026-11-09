---- MODULE IssueTracker ----
(***************************************************************************)
(* A bounded issue tracker. Projects own a dense issue numbering. Issues   *)
(* follow backlog -> todo -> inProgress -> inReview -> done, may be        *)
(* canceled while open, and may be reopened once closed. An issue may be   *)
(* blocked by other issues of the same project; the blocking graph has no  *)
(* cycles, and approval waits for every blocker to be closed.             *)
(*                                                                         *)
(* The variables are what the application persists through its unit of    *)
(* work: the project table and the issue table. Ids are dense because the *)
(* tracker never deletes a row. Titles are "" or "text" because creation   *)
(* only reads emptiness. Timestamps are "unset" or "set". Comments are a   *)
(* count. Keys and users are drawn from small fixed sets.                 *)
(*                                                                         *)
(* Every action is one committed unit of work. An action whose guard is    *)
(* false is a refused command: the application rolls back and nothing     *)
(* changes. The trace checker uses both facts.                            *)
(***************************************************************************)

EXTENDS Integers, FiniteSets, TLC

CONSTANTS MaxProjects, MaxIssues, MaxComments

ASSUME MaxProjects \in Nat /\ MaxProjects >= 1
ASSUME MaxIssues \in Nat /\ MaxIssues >= 1
ASSUME MaxComments \in Nat

Keys == {"ENG", "OPS"}
Users == {"alice", "bob"}
Statuses == {"backlog", "todo", "inProgress", "inReview", "done", "canceled"}
Titles == {"", "text"}
Marks == {"unset", "set"}
Unassigned == "unset"

VARIABLES projects, issues

vars == <<projects, issues>>

ProjectIds == DOMAIN projects
IssueIds == DOMAIN issues
NextProject == Cardinality(ProjectIds) + 1
NextIssue == Cardinality(IssueIds) + 1

Open(status) == status \in {"backlog", "todo", "inProgress", "inReview"}
Closed(status) == status \in {"done", "canceled"}
Active(status) == status \in {"inProgress", "inReview"}

IssuesOf(p) == {i \in IssueIds : issues[i].project = p}

RECURSIVE Upstream(_, _)
Upstream(frontier, seen) ==
  IF frontier = {} THEN seen
  ELSE LET next == (UNION {issues[j].blocked_by : j \in frontier}) \ seen
       IN Upstream(next, seen \union next)

\* Every issue that blocks i, directly or through other blockers.
TransitiveBlockers(i) == Upstream(issues[i].blocked_by, issues[i].blocked_by)

IsProject(record) ==
  /\ record.key \in Keys
  /\ record.next \in 1..(MaxIssues + 1)

IsIssue(record) ==
  /\ record.project \in ProjectIds
  /\ record.number \in 1..MaxIssues
  /\ record.title \in Titles
  /\ record.status \in Statuses
  /\ record.assignee \in Users \union {Unassigned}
  /\ record.blocked_by \subseteq IssueIds
  /\ record.comments \in Nat
  /\ record.started_at \in Marks
  /\ record.completed_at \in Marks
  /\ record.canceled_at \in Marks

TypeOK ==
  /\ ProjectIds = 1..Cardinality(ProjectIds)
  /\ Cardinality(ProjectIds) <= MaxProjects
  /\ IssueIds = 1..Cardinality(IssueIds)
  /\ Cardinality(IssueIds) <= MaxIssues
  /\ \A p \in ProjectIds : IsProject(projects[p])
  /\ \A i \in IssueIds : IsIssue(issues[i])

KeysUnique ==
  \A p, q \in ProjectIds : projects[p].key = projects[q].key => p = q

NumbersDense ==
  \A p \in ProjectIds :
    /\ {issues[i].number : i \in IssuesOf(p)} = 1..(projects[p].next - 1)
    /\ \A i, j \in IssuesOf(p) : issues[i].number = issues[j].number => i = j

TitleNotEmpty ==
  \A i \in IssueIds : issues[i].title /= ""

ActiveAssigned ==
  \A i \in IssueIds : Active(issues[i].status) => issues[i].assignee /= Unassigned

ActiveStarted ==
  \A i \in IssueIds : Active(issues[i].status) => issues[i].started_at = "set"

CompletedIffDone ==
  \A i \in IssueIds : (issues[i].completed_at = "set") <=> (issues[i].status = "done")

CanceledIffCanceled ==
  \A i \in IssueIds : (issues[i].canceled_at = "set") <=> (issues[i].status = "canceled")

BlockersSameProject ==
  \A i \in IssueIds : \A b \in issues[i].blocked_by : issues[b].project = issues[i].project

NoBlockCycles ==
  \A i \in IssueIds : i \notin TransitiveBlockers(i)

CommentsBounded ==
  \A i \in IssueIds : issues[i].comments <= MaxComments

Init ==
  /\ projects = [p \in {} |-> 0]
  /\ issues = [i \in {} |-> 0]

NewIssue(p, title) ==
  [
    project |-> p,
    number |-> projects[p].next,
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
  /\ Cardinality(ProjectIds) < MaxProjects
  /\ \A p \in ProjectIds : projects[p].key /= key
  /\ projects' = projects @@ (NextProject :> [key |-> key, next |-> 1])
  /\ UNCHANGED issues

CreateIssue(p, title) ==
  /\ p \in ProjectIds
  /\ title \in Titles
  /\ title /= ""
  /\ Cardinality(IssueIds) < MaxIssues
  /\ issues' = issues @@ (NextIssue :> NewIssue(p, title))
  /\ projects' = [projects EXCEPT ![p].next = @ + 1]

Triage(i) ==
  /\ i \in IssueIds
  /\ issues[i].status = "backlog"
  /\ issues' = [issues EXCEPT ![i].status = "todo"]
  /\ UNCHANGED projects

Assign(i, user) ==
  /\ i \in IssueIds
  /\ user \in Users
  /\ Open(issues[i].status)
  /\ issues[i].assignee /= user
  /\ issues' = [issues EXCEPT ![i].assignee = user]
  /\ UNCHANGED projects

Unassign(i) ==
  /\ i \in IssueIds
  /\ issues[i].assignee /= Unassigned
  /\ ~Active(issues[i].status)
  /\ issues' = [issues EXCEPT ![i].assignee = Unassigned]
  /\ UNCHANGED projects

Start(i) ==
  /\ i \in IssueIds
  /\ issues[i].status = "todo"
  /\ issues[i].assignee /= Unassigned
  /\ issues' = [issues EXCEPT ![i].status = "inProgress", ![i].started_at = "set"]
  /\ UNCHANGED projects

SubmitReview(i) ==
  /\ i \in IssueIds
  /\ issues[i].status = "inProgress"
  /\ issues' = [issues EXCEPT ![i].status = "inReview"]
  /\ UNCHANGED projects

RequestChanges(i) ==
  /\ i \in IssueIds
  /\ issues[i].status = "inReview"
  /\ issues' = [issues EXCEPT ![i].status = "inProgress"]
  /\ UNCHANGED projects

Approve(i) ==
  /\ i \in IssueIds
  /\ issues[i].status = "inReview"
  /\ \A b \in issues[i].blocked_by : Closed(issues[b].status)
  /\ issues' = [issues EXCEPT ![i].status = "done", ![i].completed_at = "set"]
  /\ UNCHANGED projects

Cancel(i) ==
  /\ i \in IssueIds
  /\ Open(issues[i].status)
  /\ issues' = [issues EXCEPT ![i].status = "canceled", ![i].canceled_at = "set"]
  /\ UNCHANGED projects

Reopen(i) ==
  /\ i \in IssueIds
  /\ Closed(issues[i].status)
  /\ issues' = [issues EXCEPT
       ![i].status = "todo",
       ![i].completed_at = "unset",
       ![i].canceled_at = "unset"]
  /\ UNCHANGED projects

AddBlocker(i, b) ==
  /\ i \in IssueIds
  /\ b \in IssueIds
  /\ i /= b
  /\ issues[i].project = issues[b].project
  /\ Open(issues[i].status)
  /\ b \notin issues[i].blocked_by
  /\ i \notin TransitiveBlockers(b)
  /\ issues' = [issues EXCEPT ![i].blocked_by = @ \union {b}]
  /\ UNCHANGED projects

RemoveBlocker(i, b) ==
  /\ i \in IssueIds
  /\ b \in issues[i].blocked_by
  /\ issues' = [issues EXCEPT ![i].blocked_by = @ \ {b}]
  /\ UNCHANGED projects

Comment(i) ==
  /\ i \in IssueIds
  /\ issues[i].status /= "canceled"
  /\ issues' = [issues EXCEPT ![i].comments = @ + 1]
  /\ UNCHANGED projects

Next ==
  \/ \E key \in Keys : CreateProject(key)
  \/ \E p \in ProjectIds, title \in Titles : CreateIssue(p, title)
  \/ \E i \in IssueIds : Triage(i)
  \/ \E i \in IssueIds, user \in Users : Assign(i, user)
  \/ \E i \in IssueIds : Unassign(i)
  \/ \E i \in IssueIds : Start(i)
  \/ \E i \in IssueIds : SubmitReview(i)
  \/ \E i \in IssueIds : RequestChanges(i)
  \/ \E i \in IssueIds : Approve(i)
  \/ \E i \in IssueIds : Cancel(i)
  \/ \E i \in IssueIds : Reopen(i)
  \/ \E i, b \in IssueIds : AddBlocker(i, b)
  \/ \E i, b \in IssueIds : RemoveBlocker(i, b)
  \/ \E i \in IssueIds : Comment(i)

Spec == Init /\ [][Next]_vars

=============================================================================
