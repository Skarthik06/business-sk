# kotlinx.serialization — keep generated serializers of our @Serializable models.
-keepattributes *Annotation*, InnerClasses
-keepclassmembers class com.businesssk.helper.** {
    *** Companion;
}
-keepclasseswithmembers class com.businesssk.helper.** {
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class com.businesssk.helper.**$$serializer { *; }

# OkHttp ships its own consumer rules; silence optional platform warnings.
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**
