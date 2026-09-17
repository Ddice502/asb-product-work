# Comment Fence Note

Prose before the comment, long enough to clear the readable signal gate.

<!-- SECRETMARKER begins here
```
SECRETMARKER inside a fence inside the comment
```
and the comment ends here -->

Prose after the comment, which must survive intact and be indexed.

<!-- an odd number of fence delimiters inside a comment
```
closes here -->

LATCHPROSE <!--LATCHSECRET--> after the odd-fence comment.

Two openers on one line: KEEPTWO <!--FIRSTSECRET <!--SECONDSECRET
and that comment closes here --> FIRSTTAIL --> SECONDTAIL both remain.

ENCLOSEDLINE <!-- ENCLOSEDSECRET --> and then an opener <!-- that never closes.

ORPHAN_TAIL is the last sentence and it must not be swallowed.
