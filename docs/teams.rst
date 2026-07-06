.. _teams:

Teams
======================================================================

The ``teams`` app lets a user create a team, become its owner, invite
registered players (who are notified by e-mail **and** in-app notification),
and maintain the team's profile (name, slug, tag, bio, thumbnail, banner).

It follows the same layering as the ``notifications`` app: thin views delegate
to a **service layer** that owns all business rules, and team events are
surfaced through the shared ``notify()`` service.

Overview
----------------------------------------------------------------------

* One user belongs to **at most one team** – enforced at the database level by
  the ``OneToOneField`` on :class:`~prostratsix.teams.models.TeamMembership`.
* A team has exactly one **owner**; **owners and managers** may manage the team
  (invite, remove members, edit the team). Only the owner may transfer
  ownership or delete the team.
* Only **registered users** can be invited. Invitations carry a token, expire
  after seven days, and cannot be duplicated while pending.

Data model
----------------------------------------------------------------------

``Team``
    Core team profile: ``name`` (unique), ``slug`` (auto-generated from the
    name), ``tag`` (unique clan tag), ``bio``, ``thumbnail``, ``banner``,
    ``region`` and social links. Helpers: ``owner``, ``managers`` and
    ``has_management_rights(user)``.

``TeamMembership``
    Links a user to a team with a ``role`` of ``owner``, ``manager``,
    ``player`` or ``substitute``. The ``OneToOneField`` guarantees the
    one-team-per-user rule.

``TeamInvitation``
    A pending/accepted/declined/revoked/expired invitation to a registered
    user, identified by a ``token`` and validated against ``expires_at``. A
    partial unique constraint prevents more than one *pending* invitation per
    ``(team, user)``.

.. automodule:: prostratsix.teams.models
   :members:
   :noindex:

Roles and permissions
----------------------------------------------------------------------

.. list-table::
   :header-rows: 1
   :widths: 30 15 15 15 15

   * - Action
     - Owner
     - Manager
     - Player
     - Substitute
   * - Edit team / invite / remove members / revoke invitation
     - ✅
     - ✅
     - ❌
     - ❌
   * - Transfer ownership
     - ✅
     - ❌
     - ❌
     - ❌
   * - Delete team
     - ✅
     - ❌
     - ❌
     - ❌
   * - Leave team
     - ⚠️ transfer first
     - ✅
     - ✅
     - ✅

Invitation lifecycle
----------------------------------------------------------------------

1. An owner or manager invites a registered user via
   :func:`~prostratsix.teams.services.invite_member`. A ``TeamInvitation`` is
   created and the invitee receives a ``TEAM`` notification with a link to the
   invitation page; an e-mail is sent unless the user opted out
   (``NotificationPreference.email_team``).
2. The invitee opens the token link and accepts
   (:func:`~prostratsix.teams.services.accept_invitation`) or declines
   (:func:`~prostratsix.teams.services.decline_invitation`).
3. On accept, a ``TeamMembership`` with role ``player`` is created and the
   owner is notified. Expired invitations are marked ``expired`` on the next
   action attempt.

Service API
----------------------------------------------------------------------

All state changes go through the service layer, which performs the permission
and state guards and raises :class:`~prostratsix.teams.services.TeamError`
subclasses (``TeamPermissionError``, ``AlreadyInTeamError``,
``InvitationError``) that the views translate into user messages.

.. automodule:: prostratsix.teams.services
   :members:
   :noindex:

URLs
----------------------------------------------------------------------

The app is mounted under ``/teams/``:

============================================  ==================================
Name                                          Purpose
============================================  ==================================
``teams:list``                                Public team directory (search)
``teams:detail``                              Public team page + roster
``teams:create``                              Create a team (redirects users
                                              who already have one)
``teams:update``                              Edit the team (management only)
``teams:invite``                              Invite a user (management only)
``teams:invitation_detail``                   Token-based accept/decline page
``teams:invitation_accept`` / ``_decline``    Act on an invitation
``teams:revoke_invitation``                   Revoke a pending invitation
``teams:remove_member``                       Remove a member (management only)
``teams:transfer_ownership``                  Hand ownership to a member (owner)
``teams:leave``                               Leave the current team
``teams:delete``                              Delete the team (owner only)
============================================  ==================================
