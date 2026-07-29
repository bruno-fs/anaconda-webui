bootloader --timeout=1
zerombr
clearpart --all
autopart

rootpw testcase

timezone --utc Europe/Prague

%packages
@^custom-environment
domain-client-nonexisting
%end
