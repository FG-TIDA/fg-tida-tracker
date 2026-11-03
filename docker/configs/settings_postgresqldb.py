import os
DATABASES = {
    'default': {
        'HOST': 'db',
        'PORT': 5432,
        'NAME': 'datatracker',
        'ENGINE': 'django.db.backends.postgresql',
        'USER': 'django',
        'PASSWORD': os.environ['FGTIDA_DEV_DB_PASSWORD'],
    },
    'blobdb': {
        'HOST': 'blobdb',
        'PORT': 5432,
        'NAME': 'blob',
        'ENGINE': 'django.db.backends.postgresql',
        'USER': 'dt',
        'PASSWORD': os.environ['FGTIDA_DEV_DB_PASSWORD'],
    },
}
