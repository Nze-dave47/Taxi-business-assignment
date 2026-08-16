from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import PasswordChangeForm
from .models import UserProfile


class UserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ['profile_picture']

    def clean_profile_picture(self):
        pic = self.cleaned_data.get('profile_picture')
        if not pic:
            return pic

        # Basic validation: content type image/* and size limit (2MB)
        content_type = getattr(pic, 'content_type', '')
        if content_type and not content_type.startswith('image/'):
            raise forms.ValidationError('Uploaded file must be an image.')

        max_size = 2 * 1024 * 1024
        if hasattr(pic, 'size') and pic.size > max_size:
            raise forms.ValidationError('Image file too large (max 2MB).')

        return pic


class SimplePasswordChangeForm(PasswordChangeForm):
    # Use Django's built-in PasswordChangeForm for validation and handling
    pass
