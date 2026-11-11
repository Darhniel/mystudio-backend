"""Role-based permissions for staff accounts.

Roles describe business authority (see ``apps.accounts.models.Role``). Keep
permission logic here rather than inside views so later milestones can declare
booking and payment rules from the same building blocks.
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.accounts.models import Role

#: Every administrative role may read the catalogue.
ADMINISTRATIVE_ROLES = frozenset({Role.OWNER, Role.MANAGER, Role.STAFF})

#: Only these roles may change catalogue records.
CATALOGUE_WRITE_ROLES = frozenset({Role.OWNER, Role.MANAGER})


class HasRole(BasePermission):
    """Allow access only to authenticated users with one of ``allowed_roles``."""

    allowed_roles = frozenset()
    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.role in self.allowed_roles
        )


class RoleBasedPermission(HasRole):
    """Reusable permission with separate read and write roles.

    Subclasses set ``read_roles`` and ``write_roles``. Safe methods (GET, HEAD,
    OPTIONS) are checked against ``read_roles``; every other method is checked
    against ``write_roles``. Authenticated users without an allowed role are
    rejected before they reach the view.
    """

    read_roles = frozenset()
    write_roles = frozenset()

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        allowed_roles = (
            self.read_roles
            if request.method in SAFE_METHODS
            else self.write_roles
        )
        return user.role in allowed_roles


class IsOwner(HasRole):
    allowed_roles = frozenset({Role.OWNER})


class IsManager(HasRole):
    allowed_roles = frozenset({Role.MANAGER})


class IsStaffRole(HasRole):
    allowed_roles = frozenset({Role.STAFF})


class IsOwnerOrManager(HasRole):
    allowed_roles = CATALOGUE_WRITE_ROLES


class IsAdministrativeStaff(RoleBasedPermission):
    """Any owner, manager or staff account may read administrative data."""

    read_roles = ADMINISTRATIVE_ROLES
    write_roles = frozenset()


class CanManageCatalogue(RoleBasedPermission):
    """Owner and manager may write; staff have read-only administrative access."""

    read_roles = ADMINISTRATIVE_ROLES
    write_roles = CATALOGUE_WRITE_ROLES
