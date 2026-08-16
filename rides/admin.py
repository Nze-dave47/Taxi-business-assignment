from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from .models import Booking, Cab, UserProfile
from .models import RideAudit


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        role = self.data.get("role") or getattr(self.instance, "role", None)

        if role == "PASSENGER":
            self.fields["is_available"].widget = forms.HiddenInput()
            self.fields["is_available"].required = False
            self.fields["is_available"].disabled = True
            self.fields["is_available"].help_text = "Passengers do not use availability."


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    form = UserProfileForm
    can_delete = False
    extra = 0

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        profile = getattr(obj, "profile", None) if obj is not None else None
        if profile is not None and profile.role == "PASSENGER":
            for form in formset.forms:
                form.base_fields["is_available"].widget = forms.HiddenInput()
                form.base_fields["is_available"].disabled = True
                form.base_fields["is_available"].help_text = "Passengers do not use availability."
        return formset


class CustomUserAdmin(BaseUserAdmin):
    inlines = [UserProfileInline]

    def get_inline_instances(self, request, obj=None):
        if obj is None:
            return []
        return [inline(self.modeladmin, request, None) for inline in self.inlines]


admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "is_available")
    list_filter = ("role", "is_available")
    search_fields = ("user__username", "user__first_name", "user__last_name")


@admin.register(Cab)
class CabAdmin(admin.ModelAdmin):
    list_display = ("driver_name", "car_model", "status")
    list_filter = ("status",)


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("passenger", "driver", "cab", "pickup_time", "status")
    list_filter = ("status", "pickup_time")


@admin.register(RideAudit)
class RideAuditAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "action", "actor", "booking")
    list_filter = ("action",)
    search_fields = ("actor__username", "details", "booking__id")
    readonly_fields = ("timestamp", "action", "actor", "actor_profile", "booking", "details")
